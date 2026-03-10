"""
shared/browser_provider.py — BrowserBase cloud browser session management.

Provides a high-level async interface to the BrowserBase REST API and
Playwright CDP connection.

Usage::

    provider = BrowserbaseProvider(api_key="bb-...")
    browser = await provider.get_browser()

    # ... use browser ...

    await provider.close_session(session_id)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp
from playwright.async_api import Browser, Playwright, async_playwright

from computeruse.exceptions import BrowserError, APIError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.browserbase.com/v1"

# How long (seconds) to wait for BrowserBase API responses.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)


class BrowserbaseProvider:
    """Manages BrowserBase cloud browser sessions and Playwright connections.

    Each provider instance owns a single :class:`Playwright` context that is
    started on the first :meth:`get_browser` call and stopped when
    :meth:`close_playwright` is called.  The Playwright instance is shared
    across all browsers opened through this provider so there is no per-call
    startup overhead after the first use.

    Example::

        provider = BrowserbaseProvider(api_key=os.environ["BROWSERBASE_API_KEY"])

        session = await provider.create_session()
        browser = await provider.get_browser(session_id=session["session_id"])

        page = await browser.new_page()
        await page.goto("https://example.com")

        await provider.close_session(session["session_id"])
        await provider.close_playwright()
    """

    def __init__(self, api_key: str) -> None:
        """
        Args:
            api_key: BrowserBase API key.  Must be non-empty.

        Raises:
            ValueError: If *api_key* is empty or whitespace.
        """
        if not api_key or not api_key.strip():
            raise ValueError("BrowserbaseProvider requires a non-empty api_key")

        self.api_key = api_key
        self._playwright: Optional[Playwright] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(self, proxy: Optional[str] = None) -> Dict[str, str]:
        """Create a new BrowserBase cloud browser session.

        Args:
            proxy: Optional proxy URL to route browser traffic through,
                   e.g. ``"http://user:pass@proxy.example.com:8080"``.

        Returns:
            A dict with two keys:

            * ``"session_id"`` — opaque BrowserBase session identifier.
            * ``"ws_endpoint"`` — ``wss://`` URL for Playwright CDP connection.

        Raises:
            APIError:      If the BrowserBase API returns a non-2xx response.
            BrowserError:  If the response is missing expected fields.
        """
        body: Dict[str, Any] = {"keepAlive": True}
        if proxy:
            body["proxy"] = proxy

        data = await self._request("POST", "/sessions", json=body)

        session_id: Optional[str] = data.get("id")
        ws_endpoint: Optional[str] = data.get("wsUrl") or data.get("ws_url")

        if not session_id or not ws_endpoint:
            raise BrowserError(
                "BrowserBase /sessions response is missing 'id' or 'wsUrl'. "
                f"Keys received: {list(data.keys())}"
            )

        logger.info("BrowserBase session created: %s", session_id)
        return {"session_id": session_id, "ws_endpoint": ws_endpoint}

    async def get_browser(
        self, session_id: Optional[str] = None, proxy: Optional[str] = None
    ) -> Browser:
        """Return a Playwright :class:`Browser` connected to a BrowserBase session.

        If *session_id* is ``None`` a new session is created automatically.
        The internal Playwright instance is started on the first call and
        reused on subsequent calls.

        Args:
            session_id: Existing BrowserBase session ID to connect to.  When
                        ``None`` a fresh session is created via
                        :meth:`create_session`.
            proxy:      Proxy URL forwarded to :meth:`create_session` when a
                        new session is being created.  Ignored if *session_id*
                        is supplied.

        Returns:
            A connected Playwright :class:`Browser` instance.

        Raises:
            APIError:      If session creation fails.
            BrowserError:  If the CDP connection cannot be established.
        """
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.debug("Playwright started")

        if session_id is None:
            session = await self.create_session(proxy=proxy)
            ws_endpoint = session["ws_endpoint"]
            session_id = session["session_id"]
        else:
            # Fetch the ws_endpoint for an existing session.
            details = await self.get_session(session_id)
            ws_endpoint = details.get("wsUrl") or details.get("ws_url") or ""
            if not ws_endpoint:
                raise BrowserError(
                    f"Could not determine ws_endpoint for session '{session_id}'. "
                    f"Response keys: {list(details.keys())}"
                )

        try:
            browser = await self._playwright.chromium.connect_over_cdp(ws_endpoint)
            logger.info(
                "Connected to BrowserBase session %s via CDP", session_id
            )
            return browser
        except Exception as exc:
            raise BrowserError(
                f"Failed to connect to BrowserBase session '{session_id}' "
                f"at '{ws_endpoint}': {exc}"
            ) from exc

    async def close_session(self, session_id: str) -> bool:
        """Terminate a BrowserBase session.

        Args:
            session_id: The session to close.

        Returns:
            ``True`` if the session was closed successfully, ``False`` if the
            session was not found (404).

        Raises:
            APIError: On non-404 HTTP errors from the BrowserBase API.
        """
        try:
            await self._request("DELETE", f"/sessions/{session_id}")
            logger.info("BrowserBase session closed: %s", session_id)
            return True
        except APIError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "close_session: session '%s' not found (already closed?)",
                    session_id,
                )
                return False
            raise

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """Return all active BrowserBase sessions for this API key.

        Returns:
            List of session dicts as returned by the BrowserBase API.  Each
            dict contains at least ``"id"`` and ``"status"`` keys.

        Raises:
            APIError: If the BrowserBase API returns a non-2xx response.
        """
        data = await self._request("GET", "/sessions")

        # The API may return a top-level list or wrap it in a dict.
        if isinstance(data, list):
            sessions: List[Dict[str, Any]] = data
        else:
            sessions = data.get("sessions", data.get("data", []))

        logger.debug("Listed %d BrowserBase session(s)", len(sessions))
        return sessions

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Fetch details for a single BrowserBase session.

        Args:
            session_id: The session to look up.

        Returns:
            Session detail dict from the BrowserBase API.

        Raises:
            APIError: If the session is not found or another API error occurs.
        """
        return await self._request("GET", f"/sessions/{session_id}")

    async def close_playwright(self) -> None:
        """Stop the internal Playwright instance.

        Should be called when the provider is no longer needed (e.g. on
        application shutdown).  Safe to call even if Playwright was never
        started.
        """
        if self._playwright is not None:
            try:
                await self._playwright.stop()
                logger.debug("Playwright stopped")
            except Exception as exc:
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200, 201, 204),
    ) -> Any:
        """Send an authenticated request to the BrowserBase REST API.

        Args:
            method:           HTTP verb (``"GET"``, ``"POST"``, ``"DELETE"``).
            path:             API path, starting with ``/`` (appended to
                              :data:`_BASE_URL`).
            json:             Optional dict to send as a JSON request body.
            expected_statuses: HTTP status codes that indicate success.

        Returns:
            Parsed JSON response body as a Python object (dict or list), or
            an empty dict for ``204 No Content`` responses.

        Raises:
            APIError:  If the response status is not in *expected_statuses*.
            BrowserError: On network-level failures.
        """
        url = f"{_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as http:
                async with http.request(
                    method, url, headers=headers, json=json
                ) as response:
                    status_code = response.status

                    # 204 No Content — nothing to parse.
                    if status_code == 204:
                        return {}

                    # Attempt to parse body as JSON regardless of status so we
                    # can include it in error messages.
                    try:
                        body = await response.json(content_type=None)
                    except Exception:
                        body = {"raw": await response.text()}

                    if status_code not in expected_statuses:
                        message = (
                            body.get("message")
                            or body.get("error")
                            or f"HTTP {status_code}"
                        ) if isinstance(body, dict) else str(body)

                        raise APIError(
                            message=f"BrowserBase API error on {method} {path}: {message}",
                            status_code=status_code,
                            response=body if isinstance(body, dict) else {"raw": body},
                        )

                    return body

        except APIError:
            raise
        except aiohttp.ClientConnectionError as exc:
            raise BrowserError(
                f"Network error connecting to BrowserBase API ({method} {path}): {exc}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise BrowserError(
                f"HTTP client error on {method} {path}: {exc}"
            ) from exc
