"""workers/backends — Pluggable backend protocol for browser automation engines."""

from workers.backends.protocol import BackendCapabilities, CUABackend
from workers.backends.registry import backend_for_task

__all__ = ["BackendCapabilities", "CUABackend", "backend_for_task"]
