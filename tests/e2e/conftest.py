"""
tests/e2e/conftest.py — Shared fixtures for E2E tests.

Environment variables:
    E2E_BASE_URL  — API base URL (default: http://localhost:8000)
    E2E_API_KEY   — Valid API key for the target environment
"""

from __future__ import annotations

import os

import httpx
import pytest

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
E2E_API_KEY = os.environ.get("E2E_API_KEY", "")


@pytest.fixture(scope="session")
def base_url() -> str:
    return E2E_BASE_URL


@pytest.fixture(scope="session")
def api_key() -> str:
    if not E2E_API_KEY:
        pytest.skip("E2E_API_KEY not set")
    return E2E_API_KEY


@pytest.fixture
def client(base_url: str, api_key: str) -> httpx.Client:
    """Pre-configured httpx client with auth header."""
    with httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=60.0,
    ) as c:
        yield c
