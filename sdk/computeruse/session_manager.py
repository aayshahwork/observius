from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from playwright.async_api import Page

from computeruse.exceptions import SessionError

logger = logging.getLogger(__name__)


class SessionManager:
    """Persists and restores browser session state (cookies + Web Storage) to disk.

    Each domain's session is stored as a single JSON file under *storage_dir*.
    The file captures cookies (via the Playwright context API) together with
    ``localStorage`` and ``sessionStorage`` key/value pairs extracted by
    evaluating JavaScript on the page.

    Typical usage::

        manager = SessionManager("./sessions")

        # After a successful login:
        await manager.save_session(page, "https://example.com")

        # On the next run, before navigating:
        restored = await manager.load_session(page, "https://example.com")
    """

    def __init__(self, storage_dir: str = "./sessions") -> None:
        """
        Args:
            storage_dir: Directory where session JSON files are written.
                         Created automatically if it does not already exist.
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save_session(self, page: Page, domain: str) -> None:
        """Capture and persist the current browser session for *domain*.

        Extracts all cookies visible to the page's browser context, then
        evaluates JavaScript to read ``localStorage`` and ``sessionStorage``
        for the current origin.  The result is written atomically (write to a
        temporary file, then rename) to avoid corrupt state if the process is
        interrupted mid-write.

        Args:
            page:   The Playwright ``Page`` whose session should be saved.
                    The page must already be navigated to *domain*.
            domain: The domain (or full origin) this session belongs to,
                    e.g. ``"https://example.com"`` or ``"example.com"``.

        Raises:
            SessionError: If cookies or storage cannot be extracted, or if the
                          file cannot be written.
        """
        try:
            cookies = await page.context.cookies()

            local_storage: dict[str, str] = await page.evaluate(
                """() => {
                    const entries = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        if (key !== null) {
                            entries[key] = localStorage.getItem(key) ?? '';
                        }
                    }
                    return entries;
                }"""
            )

            session_storage: dict[str, str] = await page.evaluate(
                """() => {
                    const entries = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        if (key !== null) {
                            entries[key] = sessionStorage.getItem(key) ?? '';
                        }
                    }
                    return entries;
                }"""
            )

            payload = {
                "domain": domain,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cookies": cookies,
                "local_storage": local_storage,
                "session_storage": session_storage,
            }

            session_path = self._get_session_path(domain)
            tmp_path = session_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(session_path)

            logger.info("Session saved for '%s' → %s", domain, session_path)

        except SessionError:
            raise
        except Exception as exc:
            raise SessionError(f"Failed to save session for '{domain}': {exc}") from exc

    async def load_session(self, page: Page, domain: str) -> bool:
        """Restore a previously saved session for *domain* into *page*.

        Loads cookies into the browser context, navigates to the domain so
        that the correct origin is active, then injects ``localStorage`` and
        ``sessionStorage`` via JavaScript.

        Args:
            page:   The Playwright ``Page`` that should receive the session.
            domain: The domain whose session should be restored.

        Returns:
            ``True`` if a session file was found and successfully applied.
            ``False`` if no session file exists for *domain*.

        Raises:
            SessionError: If the session file exists but is corrupt, or if
                          Playwright operations fail during restoration.
        """
        session_path = self._get_session_path(domain)
        if not session_path.exists():
            logger.debug("No saved session found for '%s'", domain)
            return False

        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))

            cookies = payload.get("cookies", [])
            if cookies:
                await page.context.add_cookies(cookies)

            # Navigate first so localStorage/sessionStorage writes land on the
            # correct origin; skip if the page is already there.
            origin = _ensure_scheme(domain)
            if page.url.rstrip("/") != origin.rstrip("/"):
                await page.goto(origin, wait_until="domcontentloaded")

            local_storage: dict[str, str] = payload.get("local_storage", {})
            if local_storage:
                await page.evaluate(
                    """(entries) => {
                        for (const [key, value] of Object.entries(entries)) {
                            localStorage.setItem(key, value);
                        }
                    }""",
                    local_storage,
                )

            session_storage: dict[str, str] = payload.get("session_storage", {})
            if session_storage:
                await page.evaluate(
                    """(entries) => {
                        for (const [key, value] of Object.entries(entries)) {
                            sessionStorage.setItem(key, value);
                        }
                    }""",
                    session_storage,
                )

            logger.info("Session restored for '%s' from %s", domain, session_path)
            return True

        except (json.JSONDecodeError, KeyError) as exc:
            raise SessionError(
                f"Session file for '{domain}' is corrupt or malformed: {exc}"
            ) from exc
        except SessionError:
            raise
        except Exception as exc:
            raise SessionError(f"Failed to load session for '{domain}': {exc}") from exc

    def list_sessions(self) -> List[str]:
        """Return the domain names of all persisted sessions.

        Reconstructs the original domain string from each ``*.json`` filename
        by reversing the sanitisation applied in :meth:`_get_session_path`.

        Returns:
            A sorted list of domain strings, e.g. ``["example.com", "other.com"]``.
        """
        sessions = []
        for path in sorted(self.storage_dir.glob("*.json")):
            # Reverse sanitisation: underscores back to dots where appropriate.
            # We store the canonical domain inside the file, so prefer that.
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(payload.get("domain", path.stem))
            except (json.JSONDecodeError, OSError):
                # Fall back to filename-derived name if the file is unreadable.
                sessions.append(path.stem.replace("_", "."))
        return sessions

    def delete_session(self, domain: str) -> bool:
        """Delete the saved session file for *domain*.

        Args:
            domain: The domain whose session should be removed.

        Returns:
            ``True`` if the file existed and was deleted.
            ``False`` if no session file was found for *domain*.

        Raises:
            SessionError: If the file exists but cannot be deleted (e.g. due
                          to a permissions error).
        """
        session_path = self._get_session_path(domain)
        if not session_path.exists():
            logger.debug("delete_session: no file found for '%s'", domain)
            return False

        try:
            session_path.unlink()
            logger.info("Session deleted for '%s'", domain)
            return True
        except OSError as exc:
            raise SessionError(
                f"Could not delete session file for '{domain}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_session_path(self, domain: str) -> Path:
        """Return the ``Path`` to the session JSON file for *domain*.

        The domain is sanitised so that it is safe to use as a filename on all
        platforms.  The scheme (``https://``, ``http://``), slashes, colons,
        and any other non-alphanumeric characters (except hyphens and dots) are
        replaced with underscores.

        Args:
            domain: Raw domain or origin string.

        Returns:
            Absolute ``Path`` pointing to ``<storage_dir>/<sanitized>.json``.
        """
        sanitized = _sanitize_domain(domain)
        return self.storage_dir / f"{sanitized}.json"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _sanitize_domain(domain: str) -> str:
    """Convert *domain* to a filesystem-safe filename stem.

    Steps:
    1. Strip leading/trailing whitespace.
    2. Remove scheme (``https://``, ``http://``).
    3. Replace any character that is not alphanumeric, ``-``, or ``.`` with ``_``.
    4. Collapse consecutive underscores to one.
    5. Strip leading/trailing underscores.

    Examples::

        >>> _sanitize_domain("https://example.com/login")
        'example.com_login'
        >>> _sanitize_domain("example.com:8080")
        'example.com_8080'
    """
    domain = domain.strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"[^\w\-\.]", "_", domain)
    domain = re.sub(r"_+", "_", domain)
    return domain.strip("_")


def _ensure_scheme(domain: str) -> str:
    """Prepend ``https://`` to *domain* if no scheme is present.

    Args:
        domain: Raw domain string, with or without a scheme.

    Returns:
        A URL string guaranteed to start with ``http://`` or ``https://``.
    """
    if re.match(r"^https?://", domain):
        return domain
    return f"https://{domain}"
