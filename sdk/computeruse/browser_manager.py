from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from computeruse.config import settings
from computeruse.exceptions import BrowserError

logger = logging.getLogger(__name__)

# A realistic desktop user-agent string used for stealth mode.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_BROWSERBASE_API_URL = "https://api.browserbase.com/v1/sessions"


class BrowserManager:
    """Manages the full lifecycle of a Playwright browser instance.

    Supports two operating modes:

    * **Local** — launches a Chromium process via Playwright with a set of
      anti-detection patches applied.
    * **Cloud** — connects to a `BrowserBase <https://browserbase.com>`_ remote
      browser over Chrome DevTools Protocol (CDP) using a short-lived session.

    Typical usage::

        manager = BrowserManager(headless=True)
        browser = await manager.setup_browser(use_cloud=False)
        # … use browser …
        await manager.close_browser(browser)

    The :class:`BrowserManager` does not own the Playwright launcher object;
    callers that need fine-grained lifecycle control should manage
    :func:`async_playwright` themselves and pass a :class:`Browser` instance
    directly to :meth:`close_browser`.
    """

    def __init__(
        self,
        headless: bool = True,
        browserbase_api_key: Optional[str] = None,
    ) -> None:
        """
        Args:
            headless:            Run the browser without a visible window.
                                 Ignored when connecting to a cloud browser.
            browserbase_api_key: BrowserBase API key.  Falls back to
                                 ``settings.BROWSERBASE_API_KEY`` when ``None``.
        """
        self.headless = headless
        self.browserbase_api_key = (
            browserbase_api_key or settings.BROWSERBASE_API_KEY
        )
        # Holds the Playwright manager so it can be stopped on close.
        self._playwright: Optional[Playwright] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup_browser(self, use_cloud: bool = False) -> Browser:
        """Launch or connect to a browser and return the :class:`Browser` object.

        When *use_cloud* is ``True`` **and** a BrowserBase API key is available
        the manager creates a remote BrowserBase session and connects via CDP.
        Otherwise a local Chromium instance is launched with anti-bot patches.

        Args:
            use_cloud: Attempt to use a BrowserBase cloud browser.

        Returns:
            A connected :class:`playwright.async_api.Browser` instance.

        Raises:
            BrowserError: If the browser cannot be launched or connected to.
        """
        try:
            self._playwright = await async_playwright().start()

            if use_cloud and self.browserbase_api_key:
                logger.info("Connecting to BrowserBase cloud browser…")
                return await self._connect_cloud(self._playwright)

            logger.info("Launching local Chromium (headless=%s)…", self.headless)
            return await self._launch_local(self._playwright)

        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"Failed to set up browser: {exc}") from exc

    async def create_browserbase_session(self) -> Dict[str, str]:
        """Create a new BrowserBase session and return its connection details.

        Makes a POST request to the BrowserBase sessions API and extracts the
        session identifier and the WebSocket endpoint URL needed for a CDP
        connection.

        Returns:
            A dict with two keys:

            * ``"session_id"`` — the opaque BrowserBase session identifier.
            * ``"ws_endpoint"`` — the ``wss://`` URL to pass to
              :meth:`playwright.async_api.Playwright.chromium.connect_over_cdp`.

        Raises:
            BrowserError: If no API key is configured, if the HTTP request
                          fails, or if the response is missing expected fields.
        """
        if not self.browserbase_api_key:
            raise BrowserError(
                "BrowserBase API key is not configured. "
                "Set BROWSERBASE_API_KEY in your environment or .env file."
            )

        headers = {
            "x-bb-api-key": self.browserbase_api_key,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    _BROWSERBASE_API_URL,
                    headers=headers,
                    json={"browserSettings": {}},
                ) as response:
                    if response.status not in (200, 201):
                        body = await response.text()
                        raise BrowserError(
                            f"BrowserBase API returned HTTP {response.status}: {body}"
                        )
                    data: Dict[str, Any] = await response.json()

        except aiohttp.ClientError as exc:
            raise BrowserError(
                f"Network error while creating BrowserBase session: {exc}"
            ) from exc

        session_id: Optional[str] = data.get("id")
        ws_endpoint: Optional[str] = data.get("wsUrl") or data.get("ws_url")

        if not session_id or not ws_endpoint:
            raise BrowserError(
                "BrowserBase response is missing 'id' or 'wsUrl'. "
                f"Response keys received: {list(data.keys())}"
            )

        logger.debug("BrowserBase session created: id=%s", session_id)
        return {"session_id": session_id, "ws_endpoint": ws_endpoint}

    async def close_browser(self, browser: Browser) -> None:
        """Close all pages, the browser, and the underlying Playwright instance.

        Errors during individual page or browser close calls are logged but
        not re-raised so that cleanup always completes.

        Args:
            browser: The :class:`Browser` instance to shut down.
        """
        # Close every open page first to allow orderly unload handlers to fire.
        for context in browser.contexts:
            for page in context.pages:
                try:
                    await page.close()
                except Exception as exc:
                    logger.warning("Error closing page: %s", exc)

        try:
            await browser.close()
            logger.info("Browser closed successfully")
        except Exception as exc:
            logger.warning("Error closing browser: %s", exc)

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None

    def configure_stealth_mode(self, context: BrowserContext) -> None:
        """Apply anti-detection patches to a browser context synchronously.

        Registers a script that runs in every new page before any other
        JavaScript executes.  The script:

        * Removes the ``navigator.webdriver`` flag that headless browsers expose.
        * Spoofs ``navigator.plugins`` with a realistic plugin list.
        * Spoofs ``navigator.languages`` to match the configured locale.
        * Overrides the ``chrome`` runtime object so pages that probe for it
          find a realistic value.
        * Patches ``Notification.permission`` to ``"default"`` rather than
          ``"denied"`` (the headless default).

        Args:
            context: The :class:`BrowserContext` to patch.  Call this method
                     immediately after creating the context, before opening
                     any pages.
        """
        stealth_script = """
            // Remove the navigator.webdriver flag
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true,
            });

            // Spoof a realistic plugin list
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const makePlugin = (name, filename, desc) => {
                        const plugin = { name, filename, description: desc, length: 1 };
                        plugin[0] = { type: 'application/x-google-chrome-pdf',
                                      suffixes: 'pdf', description: 'Portable Document Format' };
                        return plugin;
                    };
                    return [
                        makePlugin('Chrome PDF Plugin',
                                   'internal-pdf-viewer', 'Portable Document Format'),
                        makePlugin('Chrome PDF Viewer',
                                   'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
                        makePlugin('Native Client',
                                   'internal-nacl-plugin', ''),
                    ];
                },
                configurable: true,
            });

            // Spoof navigator.languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
                configurable: true,
            });

            // Expose a minimal chrome runtime object
            if (!window.chrome) {
                window.chrome = {
                    runtime: {
                        onConnect: { addListener: () => {} },
                        onMessage: { addListener: () => {} },
                    },
                };
            }

            // Return "default" for Notification.permission rather than "denied"
            try {
                Object.defineProperty(Notification, 'permission', {
                    get: () => 'default',
                    configurable: true,
                });
            } catch (_) {}
        """
        context.add_init_script(stealth_script)
        logger.debug("Stealth mode patches registered on context")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _launch_local(self, playwright: Playwright) -> Browser:
        """Launch a local Chromium instance with hardened launch arguments."""
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1920,1080",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ]

        try:
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
        except Exception as exc:
            raise BrowserError(f"Could not launch local Chromium: {exc}") from exc

        # Apply stealth mode to the default context that Playwright creates.
        if browser.contexts:
            self.configure_stealth_mode(browser.contexts[0])

        return browser

    async def _connect_cloud(self, playwright: Playwright) -> Browser:
        """Create a BrowserBase session and connect via CDP."""
        session = await self.create_browserbase_session()
        ws_endpoint = session["ws_endpoint"]

        try:
            browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
            logger.info(
                "Connected to BrowserBase session %s", session["session_id"]
            )
        except Exception as exc:
            raise BrowserError(
                f"Could not connect to BrowserBase endpoint '{ws_endpoint}': {exc}"
            ) from exc

        if browser.contexts:
            self.configure_stealth_mode(browser.contexts[0])

        return browser
