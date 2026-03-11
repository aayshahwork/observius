"""
workers/captcha_solver.py — CAPTCHA detection and solving via 2Captcha API.

Supports reCAPTCHA v2, hCaptcha (via 2Captcha), and Cloudflare Turnstile
(via stealth click + wait).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# 2Captcha endpoints
_IN_URL = "http://2captcha.com/in.php"
_RES_URL = "http://2captcha.com/res.php"

# Polling config
_POLL_INTERVAL_S = 5
_POLL_MAX_WAIT_S = 120
_TURNSTILE_MAX_WAIT_S = 30


@dataclass
class CaptchaResult:
    """Result of a CAPTCHA solve attempt."""

    solved: bool
    captcha_type: str  # "recaptcha_v2" | "hcaptcha" | "turnstile"
    token: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


class CaptchaSolver:
    """Detect and solve CAPTCHAs on a Playwright page."""

    def __init__(self, twocaptcha_api_key: str) -> None:
        self._api_key = twocaptcha_api_key

    async def detect_captcha(self, page: Any) -> Optional[str]:
        """Detect which CAPTCHA type (if any) is present on the page.

        Returns captcha type string or None.
        """
        # reCAPTCHA v2
        el = await page.query_selector(".g-recaptcha, iframe[src*='recaptcha']")
        if el:
            return "recaptcha_v2"

        # hCaptcha
        el = await page.query_selector(".h-captcha, iframe[src*='hcaptcha']")
        if el:
            return "hcaptcha"

        # Cloudflare Turnstile
        el = await page.query_selector(".cf-turnstile, iframe[src*='challenges.cloudflare.com']")
        if el:
            return "turnstile"

        return None

    async def solve(self, page: Any, captcha_type: Optional[str] = None) -> CaptchaResult:
        """Detect and solve a CAPTCHA. Auto-detects type if not specified."""
        if captcha_type is None:
            captcha_type = await self.detect_captcha(page)

        if captcha_type is None:
            return CaptchaResult(solved=False, captcha_type="unknown", error="no_captcha_detected")

        if captcha_type == "recaptcha_v2":
            return await self._solve_recaptcha_v2(page)
        elif captcha_type == "hcaptcha":
            return await self._solve_hcaptcha(page)
        elif captcha_type == "turnstile":
            return await self._solve_turnstile(page)
        else:
            return CaptchaResult(solved=False, captcha_type=captcha_type, error=f"unsupported_type:{captcha_type}")

    async def _solve_recaptcha_v2(self, page: Any) -> CaptchaResult:
        """Solve reCAPTCHA v2 via 2Captcha API."""
        start = time.monotonic()
        captcha_type = "recaptcha_v2"

        if not self._api_key:
            return CaptchaResult(solved=False, captcha_type=captcha_type, error="no_api_key")

        # Extract sitekey
        sitekey = await page.evaluate(
            """() => {
                const el = document.querySelector('.g-recaptcha');
                return el ? el.getAttribute('data-sitekey') : null;
            }"""
        )
        if not sitekey:
            return CaptchaResult(
                solved=False, captcha_type=captcha_type, error="sitekey_not_found",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        page_url = page.url

        # Submit to 2Captcha
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _IN_URL,
                    data={
                        "key": self._api_key,
                        "method": "userrecaptcha",
                        "googlekey": sitekey,
                        "pageurl": page_url,
                        "json": "1",
                    },
                ) as resp:
                    body = await resp.json(content_type=None)

                if body.get("status") != 1:
                    return CaptchaResult(
                        solved=False, captcha_type=captcha_type,
                        error=body.get("request", "submit_failed"),
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

                task_id = body["request"]
                logger.info("2captcha_submitted task_id=%s type=%s", task_id, captcha_type)

                # Poll for result
                token = await self._poll_result(session, task_id)
        except (aiohttp.ClientError, ValueError, KeyError) as exc:
            return CaptchaResult(
                solved=False, captcha_type=captcha_type,
                error=f"request_failed:{exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if token is None:
            return CaptchaResult(solved=False, captcha_type=captcha_type, error="solver_timeout", duration_ms=elapsed)
        if token.startswith("ERROR_"):
            return CaptchaResult(solved=False, captcha_type=captcha_type, error=token, duration_ms=elapsed)

        # Inject token
        await page.evaluate(
            """(token) => {
                const textarea = document.getElementById('g-recaptcha-response');
                if (textarea) {
                    textarea.style.display = '';
                    textarea.innerHTML = token;
                }
                // Try to invoke callback
                try {
                    const cfg = window.___grecaptcha_cfg;
                    if (cfg && cfg.clients) {
                        for (const client of Object.values(cfg.clients)) {
                            for (const val of Object.values(client)) {
                                if (val && typeof val === 'object') {
                                    for (const v of Object.values(val)) {
                                        if (v && typeof v.callback === 'function') {
                                            v.callback(token);
                                            return;
                                        }
                                    }
                                }
                            }
                        }
                    }
                } catch (e) {}
            }""",
            token,
        )

        logger.info("captcha_solved type=%s duration_ms=%d", captcha_type, elapsed)
        return CaptchaResult(solved=True, captcha_type=captcha_type, token=token, duration_ms=elapsed)

    async def _solve_hcaptcha(self, page: Any) -> CaptchaResult:
        """Solve hCaptcha via 2Captcha API."""
        start = time.monotonic()
        captcha_type = "hcaptcha"

        if not self._api_key:
            return CaptchaResult(solved=False, captcha_type=captcha_type, error="no_api_key")

        # Extract sitekey
        sitekey = await page.evaluate(
            """() => {
                const el = document.querySelector('.h-captcha');
                return el ? el.getAttribute('data-sitekey') : null;
            }"""
        )
        if not sitekey:
            return CaptchaResult(
                solved=False, captcha_type=captcha_type, error="sitekey_not_found",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        page_url = page.url

        # Submit to 2Captcha
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _IN_URL,
                    data={
                        "key": self._api_key,
                        "method": "hcaptcha",
                        "sitekey": sitekey,
                        "pageurl": page_url,
                        "json": "1",
                    },
                ) as resp:
                    body = await resp.json(content_type=None)

                if body.get("status") != 1:
                    return CaptchaResult(
                        solved=False, captcha_type=captcha_type,
                        error=body.get("request", "submit_failed"),
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

                task_id = body["request"]
                logger.info("2captcha_submitted task_id=%s type=%s", task_id, captcha_type)

                # Poll for result
                token = await self._poll_result(session, task_id)
        except (aiohttp.ClientError, ValueError, KeyError) as exc:
            return CaptchaResult(
                solved=False, captcha_type=captcha_type,
                error=f"request_failed:{exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if token is None:
            return CaptchaResult(solved=False, captcha_type=captcha_type, error="solver_timeout", duration_ms=elapsed)
        if token.startswith("ERROR_"):
            return CaptchaResult(solved=False, captcha_type=captcha_type, error=token, duration_ms=elapsed)

        # Inject token
        await page.evaluate(
            """(token) => {
                const textarea = document.querySelector('[name="h-captcha-response"]');
                if (textarea) textarea.value = token;
                // Try to invoke hcaptcha callback
                try {
                    if (window.hcaptcha) {
                        const iframes = document.querySelectorAll('iframe[src*="hcaptcha"]');
                        if (iframes.length > 0) {
                            window.hcaptcha.execute();
                        }
                    }
                } catch (e) {}
            }""",
            token,
        )

        logger.info("captcha_solved type=%s duration_ms=%d", captcha_type, elapsed)
        return CaptchaResult(solved=True, captcha_type=captcha_type, token=token, duration_ms=elapsed)

    async def _solve_turnstile(self, page: Any) -> CaptchaResult:
        """Bypass Cloudflare Turnstile with stealth click + wait.

        Turnstile is designed to pass for browsers with proper fingerprints.
        With stealth scripts active, a click and wait is usually sufficient.
        """
        start = time.monotonic()
        captcha_type = "turnstile"

        # Find the Turnstile iframe
        iframe_el = await page.query_selector(
            "iframe[src*='challenges.cloudflare.com/cdn-cgi/challenge-platform']"
        )
        if not iframe_el:
            # Try the container checkbox directly
            iframe_el = await page.query_selector(".cf-turnstile iframe")

        if not iframe_el:
            return CaptchaResult(
                solved=False, captcha_type=captcha_type, error="turnstile_iframe_not_found",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Click the iframe (Turnstile checkbox)
        try:
            box = await iframe_el.bounding_box()
            if box:
                await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await iframe_el.click()
        except Exception as exc:
            logger.warning("turnstile_click_failed error=%s", exc)

        # Poll for success token (Turnstile sets a hidden input on success)
        poll_start = time.monotonic()
        while (time.monotonic() - poll_start) < _TURNSTILE_MAX_WAIT_S:
            token = await page.evaluate(
                """() => {
                    const input = document.querySelector('[name="cf-turnstile-response"]');
                    return input ? input.value : null;
                }"""
            )
            if token:
                elapsed = int((time.monotonic() - start) * 1000)
                logger.info("captcha_solved type=%s duration_ms=%d", captcha_type, elapsed)
                return CaptchaResult(solved=True, captcha_type=captcha_type, token=token, duration_ms=elapsed)
            await asyncio.sleep(1)

        elapsed = int((time.monotonic() - start) * 1000)
        return CaptchaResult(solved=False, captcha_type=captcha_type, error="turnstile_timeout", duration_ms=elapsed)

    async def _poll_result(self, session: aiohttp.ClientSession, task_id: str) -> Optional[str]:
        """Poll 2Captcha for result. Returns token, error string, or None on timeout."""
        deadline = time.monotonic() + _POLL_MAX_WAIT_S
        # Initial delay — 2Captcha recommends waiting before first poll
        await asyncio.sleep(_POLL_INTERVAL_S)

        while time.monotonic() < deadline:
            async with session.get(
                _RES_URL,
                params={"key": self._api_key, "action": "get", "id": task_id, "json": "1"},
            ) as resp:
                body = await resp.json(content_type=None)

            request_val = body.get("request", "")

            if body.get("status") == 1:
                return request_val

            if request_val == "CAPCHA_NOT_READY":
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            # Error response
            if request_val.startswith("ERROR_"):
                return request_val

            # Unknown response — treat as not ready
            await asyncio.sleep(_POLL_INTERVAL_S)

        return None  # Timeout
