#!/bin/sh
# Worker entrypoint: starts the health check server, then launches Celery.
# Used by Railway to get an HTTP health probe on port 8001.

exec celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=2
