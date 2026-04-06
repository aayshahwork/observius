"""
workers/healthcheck.py — Lightweight HTTP health check for Celery workers.

Railway (and other orchestrators) need an HTTP endpoint to probe.
This runs a tiny server on port 8001 alongside the Celery worker.

Start alongside Celery via the entrypoint script (infra/worker-entrypoint.sh).
"""

from __future__ import annotations

import http.server
import logging
import threading

logger = logging.getLogger("pokant.worker.health")

_PORT = 8001


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Responds 200 on GET /health, 404 otherwise."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"worker"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Silence per-request access logs
        pass


def start_health_server() -> None:
    """Start the health check HTTP server in a daemon thread."""
    server = http.server.HTTPServer(("0.0.0.0", _PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Worker health check listening on :%d/health", _PORT)
