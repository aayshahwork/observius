"""
api/middleware/metrics.py — Prometheus metrics middleware for FastAPI.

Tracks: http_requests_total, http_request_duration_seconds, http_request_size_bytes.
Exposes GET /metrics endpoint for Prometheus scraping (registered in main.py).
"""

from __future__ import annotations

import re
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from prometheus_client import Counter, Histogram

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

http_request_size_bytes = Histogram(
    "http_request_size_bytes",
    "HTTP request body size in bytes",
    ["method", "path"],
    buckets=[100, 500, 1_000, 5_000, 10_000, 50_000, 100_000],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Replace UUID path segments with :id to prevent label cardinality explosion."""
    return _UUID_RE.sub(":id", path)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record Prometheus metrics for every HTTP request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = _normalize_path(request.url.path)

        # Don't track the metrics endpoint itself.
        if path == "/metrics":
            return await call_next(request)

        content_length = int(request.headers.get("content-length", 0))
        http_request_size_bytes.labels(method=request.method, path=path).observe(content_length)

        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        http_requests_total.labels(
            method=request.method,
            path=path,
            status_code=response.status_code,
        ).inc()
        http_request_duration_seconds.labels(method=request.method, path=path).observe(duration)

        return response
