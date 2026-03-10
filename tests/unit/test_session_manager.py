"""Unit tests for computeruse.session_manager.SessionManager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from computeruse.exceptions import SessionError
from computeruse.session_manager import SessionManager, _sanitize_domain, _ensure_scheme


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Temporary directory used as the session store for every test."""
    return tmp_path / "sessions"


@pytest.fixture
def manager(sessions_dir: Path) -> SessionManager:
    """SessionManager backed by a fresh temporary directory."""
    return SessionManager(storage_dir=str(sessions_dir))


def _make_page(
    url: str = "https://example.com",
    cookies: list | None = None,
    local_storage: dict | None = None,
    session_storage: dict | None = None,
) -> MagicMock:
    """Build a minimal Playwright Page mock with async context and evaluate methods."""
    page = MagicMock()

    # page.url property
    type(page).url = PropertyMock(return_value=url)

    # page.context.cookies() is async
    page.context.cookies = AsyncMock(return_value=cookies or [])

    # page.context.add_cookies() is async
    page.context.add_cookies = AsyncMock(return_value=None)

    # page.evaluate() returns different values depending on call order.
    # First call → localStorage, second call → sessionStorage.
    ls = local_storage or {}
    ss = session_storage or {}
    page.evaluate = AsyncMock(side_effect=[ls, ss])

    # page.goto() is async
    page.goto = AsyncMock(return_value=None)

    return page


# ---------------------------------------------------------------------------
# save_session
# ---------------------------------------------------------------------------

class TestSaveSession:
    @pytest.mark.asyncio
    async def test_creates_json_file(self, manager: SessionManager, sessions_dir: Path) -> None:
        page = _make_page()
        await manager.save_session(page, "example.com")

        saved = sessions_dir / "example.com.json"
        assert saved.exists()

    @pytest.mark.asyncio
    async def test_file_contains_expected_keys(self, manager: SessionManager, sessions_dir: Path) -> None:
        page = _make_page(
            cookies=[{"name": "session", "value": "abc"}],
            local_storage={"theme": "dark"},
            session_storage={"cart": "[]"},
        )
        await manager.save_session(page, "example.com")

        data = json.loads((sessions_dir / "example.com.json").read_text())
        assert data["domain"] == "example.com"
        assert data["cookies"] == [{"name": "session", "value": "abc"}]
        assert data["local_storage"] == {"theme": "dark"}
        assert data["session_storage"] == {"cart": "[]"}
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_overwrites_existing_session(self, manager: SessionManager, sessions_dir: Path) -> None:
        page1 = _make_page(cookies=[{"name": "old", "value": "v1"}])
        await manager.save_session(page1, "example.com")

        page2 = _make_page(cookies=[{"name": "new", "value": "v2"}])
        await manager.save_session(page2, "example.com")

        data = json.loads((sessions_dir / "example.com.json").read_text())
        assert data["cookies"][0]["value"] == "v2"

    @pytest.mark.asyncio
    async def test_sanitizes_https_domain(self, manager: SessionManager, sessions_dir: Path) -> None:
        page = _make_page(url="https://sub.example.com/path")
        await manager.save_session(page, "https://sub.example.com/path")

        # The sanitised filename should not contain slashes or colons.
        files = list(sessions_dir.glob("*.json"))
        assert len(files) == 1
        assert "/" not in files[0].name
        assert ":" not in files[0].name

    @pytest.mark.asyncio
    async def test_raises_session_error_on_write_failure(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        page = _make_page()
        # Make the directory read-only so writing fails.
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir.chmod(0o444)
        try:
            with pytest.raises(SessionError):
                await manager.save_session(page, "example.com")
        finally:
            sessions_dir.chmod(0o755)  # restore so tmp_path cleanup works


# ---------------------------------------------------------------------------
# load_session
# ---------------------------------------------------------------------------

class TestLoadSession:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_file(self, manager: SessionManager) -> None:
        page = _make_page()
        result = await manager.load_session(page, "nonexistent.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_round_trip_save_and_load(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        """Data saved by save_session must be restored by load_session."""
        cookies = [{"name": "auth", "value": "token123", "domain": "example.com"}]
        ls = {"theme": "dark", "lang": "en"}
        ss = {"draft": "hello"}

        save_page = _make_page(cookies=cookies, local_storage=ls, session_storage=ss)
        await manager.save_session(save_page, "example.com")

        # Build a fresh page mock for loading.
        load_page = _make_page(
            url="https://example.com",
            local_storage={},
            session_storage={},
        )
        result = await manager.load_session(load_page, "example.com")

        assert result is True
        load_page.context.add_cookies.assert_awaited_once_with(cookies)

        # evaluate is called twice: once for localStorage, once for sessionStorage.
        assert load_page.evaluate.await_count == 2
        ls_call_args = load_page.evaluate.await_args_list[0]
        ss_call_args = load_page.evaluate.await_args_list[1]
        assert ls in (ls_call_args.args + tuple(ls_call_args.kwargs.values()))
        assert ss in (ss_call_args.args + tuple(ss_call_args.kwargs.values()))

    @pytest.mark.asyncio
    async def test_navigates_when_url_differs(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        """load_session must navigate to the domain if the page is not already there."""
        page = _make_page()
        await manager.save_session(page, "example.com")

        load_page = _make_page(url="about:blank", local_storage={}, session_storage={})
        await manager.load_session(load_page, "example.com")

        load_page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_navigation_when_already_on_page(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        """load_session must NOT navigate if the page is already at the right origin."""
        page = _make_page()
        await manager.save_session(page, "https://example.com")

        load_page = _make_page(
            url="https://example.com",
            local_storage={},
            session_storage={},
        )
        await manager.load_session(load_page, "https://example.com")

        load_page.goto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_session_error_on_corrupt_json(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        corrupt = sessions_dir / "bad.com.json"
        corrupt.write_text("{not valid json", encoding="utf-8")

        load_page = _make_page(local_storage={}, session_storage={})
        with pytest.raises(SessionError, match="corrupt"):
            await manager.load_session(load_page, "bad.com")

    @pytest.mark.asyncio
    async def test_empty_cookies_skips_add_cookies(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        """No add_cookies call should be made when the saved session has no cookies."""
        page = _make_page(cookies=[], local_storage={}, session_storage={})
        await manager.save_session(page, "example.com")

        load_page = _make_page(url="https://example.com", local_storage={}, session_storage={})
        await manager.load_session(load_page, "example.com")

        load_page.context.add_cookies.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    @pytest.mark.asyncio
    async def test_empty_when_no_sessions(self, manager: SessionManager) -> None:
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_returns_saved_domains(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        for domain in ("alpha.com", "beta.com", "gamma.com"):
            page = _make_page()
            await manager.save_session(page, domain)

        result = manager.list_sessions()
        assert sorted(result) == ["alpha.com", "beta.com", "gamma.com"]

    def test_returns_domain_from_file_content(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        """list_sessions reads the 'domain' key from the JSON, not just the filename."""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "domain": "https://fancy.example.com/login",
            "created_at": "2024-01-01T00:00:00+00:00",
            "cookies": [],
            "local_storage": {},
            "session_storage": {},
        }
        (sessions_dir / "fancy.example.com_login.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        result = manager.list_sessions()
        assert "https://fancy.example.com/login" in result


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_delete_existing_session(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        page = _make_page()
        await manager.save_session(page, "example.com")

        deleted = manager.delete_session("example.com")

        assert deleted is True
        assert not (sessions_dir / "example.com.json").exists()

    def test_delete_nonexistent_session_returns_false(self, manager: SessionManager) -> None:
        result = manager.delete_session("ghost.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_deleted_session_not_in_list(
        self, manager: SessionManager, sessions_dir: Path
    ) -> None:
        page = _make_page()
        await manager.save_session(page, "to-delete.com")
        await manager.save_session(_make_page(), "to-keep.com")

        manager.delete_session("to-delete.com")

        assert "to-delete.com" not in manager.list_sessions()
        assert "to-keep.com" in manager.list_sessions()


# ---------------------------------------------------------------------------
# _sanitize_domain helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("example.com",                  "example.com"),
    ("https://example.com",          "example.com"),
    ("http://example.com",           "example.com"),
    ("https://example.com/login",    "example.com_login"),
    ("example.com:8080",             "example.com_8080"),
    ("  example.com  ",              "example.com"),
    ("https://sub.example.com/a/b",  "sub.example.com_a_b"),
])
def test_sanitize_domain(domain: str, expected: str) -> None:
    assert _sanitize_domain(domain) == expected


# ---------------------------------------------------------------------------
# _ensure_scheme helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("example.com",          "https://example.com"),
    ("http://example.com",   "http://example.com"),
    ("https://example.com",  "https://example.com"),
])
def test_ensure_scheme(domain: str, expected: str) -> None:
    assert _ensure_scheme(domain) == expected
