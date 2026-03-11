#!/usr/bin/env python3
"""
Worker health check script.

Uses Celery inspect.ping() to verify at least one worker is responsive.
Exit code 0 = healthy, 1 = unhealthy.
"""

from __future__ import annotations

import sys

from celery import Celery

from workers.config import worker_settings

app = Celery(broker=worker_settings.REDIS_URL)
inspector = app.control.inspect(timeout=5.0)

try:
    result = inspector.ping()
    if result:
        print(f"Healthy: {len(result)} worker(s) responding")
        sys.exit(0)
    else:
        print("Unhealthy: no workers responding")
        sys.exit(1)
except Exception as exc:
    print(f"Unhealthy: {exc}")
    sys.exit(1)
