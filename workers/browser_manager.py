"""
workers/browser_manager.py — Browser lifecycle management for the task executor.

Supports local headless Chromium and cloud browsers via Browserbase CDP.
Stealth scripts are loaded from workers/stealth/ at runtime.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)

_STEALTH_DIR = Path(__file__).parent / "stealth"
_BROWSERBASE_API_URL = "https://api.browserbase.com/v1/sessions"


class BrowserManager:
    """Manages browser acquisition, stealth injection, and cleanup."""

    def __init__(
        self,
        browserbase_api_key: Optional[str] = None,
        browserbase_project_id: Optional[str] = None,
    ) -> None:
        self.browserbase_api_key = browserbase_api_key
        self.browserbase_project_id = browserbase_project_id
        self._playwright: Optional[Playwright] = None
        self._session_id: Optional[str] = None
        self._stealth_scripts: Optional[list[str]] = None

    def _load_stealth_scripts(self) -> list[str]:
        """Load all .js files from the stealth directory, cached on first call."""
        if self._stealth_scripts is not None:
            return self._stealth_scripts

        scripts: list[str] = []
        if _STEALTH_DIR.is_dir():
            for js_file in sorted(_STEALTH_DIR.glob("*.js")):
                scripts.append(js_file.read_text(encoding="utf-8"))
                logger.debug("Loaded stealth script: %s", js_file.name)

        self._stealth_scripts = scripts
        return scripts

    async def get_browser(self, use_cloud: bool = False) -> Browser:
        """Acquire a browser instance. 10s timeout.

        If use_cloud and a Browserbase API key is configured, connects
        to a cloud browser via CDP. Otherwise launches local Chromium.
        """
        try:
            self._playwright = await async_playwright().start()

            if use_cloud and self.browserbase_api_key:
                return await self._connect_cloud(self._playwright)

            return await self._launch_local(self._playwright)
        except Exception as exc:
            raise RuntimeError(f"Failed to acquire browser: {exc}") from exc

    async def release_browser(self, browser: Browser) -> None:
        """Close all pages/contexts and release the browser."""
        for context in browser.contexts:
            for page in context.pages:
                try:
                    await page.close()
                except Exception as exc:
                    logger.warning("Error closing page: %s", exc)

        try:
            await browser.close()
        except Exception as exc:
            logger.warning("Error closing browser: %s", exc)

        if self._session_id and self.browserbase_api_key:
            await self._release_cloud_session()

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None

    async def apply_stealth(self, page: Page, task_id: str = "") -> None:
        """Inject stealth JavaScript into the page.

        Sets up deterministic canvas fingerprint seed from task_id,
        then loads all scripts from workers/stealth/.
        """
        seed = int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) if task_id else 42
        await page.evaluate(f"window.__stealth_seed = {seed}")
        await page.evaluate("window.__stealth_timezone = 'UTC'")

        for script in self._load_stealth_scripts():
            await page.evaluate(script)

        logger.debug("Stealth scripts applied (seed=%d)", seed)

    async def _launch_local(self, playwright: Playwright) -> Browser:
        """Launch local headless Chromium."""
        return await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
            timeout=10_000,
        )

    async def _connect_cloud(self, playwright: Playwright) -> Browser:
        """Create a Browserbase session and connect via CDP."""
        import aiohttp

        assert self.browserbase_api_key is not None
        headers: dict[str, str] = {
            "x-bb-api-key": self.browserbase_api_key,
            "Content-Type": "application/json",
        }
        body: dict = {"browserSettings": {}}
        if self.browserbase_project_id:
            body["projectId"] = self.browserbase_project_id

        async with aiohttp.ClientSession() as http:
            async with http.post(
                _BROWSERBASE_API_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status not in (200, 201):
                    text = await response.text()
                    raise RuntimeError(f"Browserbase API HTTP {response.status}: {text}")
                data = await response.json()

        self._session_id = data.get("id")
        ws_endpoint = data.get("wsUrl") or data.get("ws_url")
        if not self._session_id or not ws_endpoint:
            raise RuntimeError(f"Browserbase response missing id/wsUrl: {list(data.keys())}")

        browser = await playwright.chromium.connect_over_cdp(ws_endpoint, timeout=10_000)
        logger.info("Connected to Browserbase session %s", self._session_id)
        return browser

    async def _release_cloud_session(self) -> None:
        """Release the Browserbase session."""
        import aiohttp

        if not self._session_id:
            return
        try:
            async with aiohttp.ClientSession() as http:
                async with http.delete(
                    f"{_BROWSERBASE_API_URL}/{self._session_id}",
                    headers={"x-bb-api-key": self.browserbase_api_key or ""},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    logger.info("Browserbase session %s released (HTTP %d)", self._session_id, resp.status)
        except Exception as exc:
            logger.warning("Failed to release Browserbase session: %s", exc)
        finally:
            self._session_id = None
