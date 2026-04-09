from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from computeruse.config import settings
from computeruse.exceptions import (
    APIError,
    ComputerUseSDKError,
    NetworkError,
    TaskExecutionError,
)
from computeruse.models import TaskConfig, TaskResult
from computeruse.retry import RetryHandler

logger = logging.getLogger(__name__)

# Directory where completed TaskResult objects are cached as JSON files.
# Stored under .pokant/ to match the SDK's standard data directory.
_TASK_STORE = Path(".pokant") / "tasks"

# Hosted cloud API base URL.
_CLOUD_API_BASE = "https://pokant-production.up.railway.app/api/v1"

# Seconds between cloud-task status polls.
_POLL_INTERVAL: float = 2.0

# Hard ceiling (seconds) for cloud-task polling before giving up.
_CLOUD_POLL_TIMEOUT: int = 600

# Poll retry constants for transient network failures during polling.
_MAX_POLL_RETRIES: int = 10
_POLL_ERROR_BACKOFF: float = 5.0


class ComputerUse:
    """One-line entry point for browser automation powered by Claude.

    Two execution modes are supported:

    * **Local** (``local=True``, the default) — Playwright runs on the
      calling machine.  No ``api_key`` is required; only
      ``ANTHROPIC_API_KEY`` in the environment.
    * **Cloud** (``local=False``) — tasks are dispatched to the hosted
      *computeruse* service via HTTPS.  Requires ``api_key``.

    Quick start::

        from computeruse import ComputerUse

        cu = ComputerUse()
        result = cu.run_task(
            url="https://news.ycombinator.com",
            task="Return the titles of the top 5 posts",
            output_schema={"titles": "list[str]"},
        )
        print(result.result["titles"])

    Attributes:
        model (str): Anthropic model ID used for this client instance.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        local: bool = True,
        model: str = settings.DEFAULT_MODEL,
        headless: bool = False,
        browserbase_api_key: Optional[str] = None,
        pokant_api_url: Optional[str] = None,
        pokant_api_key: Optional[str] = None,
    ) -> None:
        """Initialise the ComputerUse client.

        Args:
            api_key: API key for the hosted cloud service.  Required when
                ``local=False``.
            local: ``True`` (default) — run tasks locally.
                ``False`` — dispatch to the cloud API.
            model: Anthropic model ID.
            headless: Run the browser without a visible window.
            browserbase_api_key: BrowserBase API key for managed remote
                browsers in local mode.
            pokant_api_url: Base URL of the Pokant API for automatic
                run reporting.  Falls back to ``POKANT_API_URL`` env var.
            pokant_api_key: API key for the Pokant ingest endpoint.
                Falls back to ``POKANT_API_KEY`` env var.

        Raises:
            ValueError: If ``local=False`` and no ``api_key`` is provided.
        """
        self.api_key = api_key
        self.local = local
        self.model = model
        self.headless = headless
        self.browserbase_api_key = browserbase_api_key or settings.BROWSERBASE_API_KEY
        self.pokant_api_url = pokant_api_url or settings.POKANT_API_URL
        self.pokant_api_key = pokant_api_key or settings.POKANT_API_KEY

        if not local and not api_key:
            raise ValueError(
                "An 'api_key' is required for cloud execution (local=False). "
                "Pass it as ComputerUse(api_key='cu-…', local=False) or set "
                "COMPUTERUSE_API_KEY in your environment."
            )

        _TASK_STORE.mkdir(parents=True, exist_ok=True)

    @property
    def _cloud_base(self) -> str:
        """Cloud API base URL, preferring user-provided pokant_api_url."""
        if self.pokant_api_url:
            return self.pokant_api_url.rstrip("/") + "/api/v1"
        return _CLOUD_API_BASE

    # ------------------------------------------------------------------
    # Public synchronous API
    # ------------------------------------------------------------------

    def run_task(
        self,
        url: str,
        task: str,
        credentials: Optional[Dict[str, str]] = None,
        output_schema: Optional[Dict[str, str]] = None,
        max_steps: int = 50,
        timeout_seconds: int = 300,
        retry_attempts: int = 3,
        retry_delay_seconds: int = 2,
        max_cost_cents: Optional[int] = None,
    ) -> TaskResult:
        """Run a browser automation task and block until it completes.

        Thin synchronous wrapper around :meth:`run_task_async`.  Safe to
        call from scripts, CLIs, and Jupyter notebooks — handles the
        event-loop gymnastics internally.

        See :meth:`run_task_async` for full parameter documentation.
        """
        return _run_sync(
            self.run_task_async(
                url=url,
                task=task,
                credentials=credentials,
                output_schema=output_schema,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_delay_seconds=retry_delay_seconds,
                max_cost_cents=max_cost_cents,
            )
        )

    def get_task(self, task_id: str) -> TaskResult:
        """Retrieve a previously executed task by its ID.

        Checks the local ``.tasks/`` cache first; falls back to the cloud API
        when ``local=False`` and the task is not cached locally.

        Raises:
            KeyError:  If the task is not found locally or in the cloud.
            APIError:  If the cloud API request fails with a non-404 error.
        """
        cached = self._load_cached_result(task_id)
        if cached is not None:
            return cached

        if not self.local:
            return _run_sync(self._fetch_cloud_task(task_id))

        raise KeyError(f"No task with id {task_id!r} found in local storage ({_TASK_STORE})")

    def list_tasks(self, limit: int = 10, status: Optional[str] = None) -> List[TaskResult]:
        """Return the most recent task results, newest first.

        Args:
            limit: Maximum number of results to return.
            status: Optional status filter (e.g. ``"completed"``, ``"failed"``).
        """
        if self.local:
            results = self._list_cached_results(limit)
            if status:
                results = [r for r in results if r.status == status]
            return results
        return _run_sync(self._list_cloud_tasks(limit, status))

    def get_replay(self, task_id: str) -> str:
        """Return the replay path (local) or URL (cloud) for a task.

        Args:
            task_id: The task ID from a previous :class:`TaskResult`.

        Returns:
            Local mode: filesystem path to the replay HTML file.
            Cloud mode: pre-signed URL for the replay recording.

        Raises:
            FileNotFoundError: If the local replay file does not exist.
            NetworkError: If a cloud API connection fails.
            ComputerUseSDKError: If the cloud API returns 404.
        """
        if self.local:
            replay_dir = Path(settings.REPLAY_DIR)
            # Check both JSON and HTML replay formats
            for suffix in (".html", ".json"):
                path = replay_dir / f"{task_id}{suffix}"
                if path.exists():
                    return str(path)
            raise FileNotFoundError(
                f"No replay found for task {task_id!r} in {replay_dir}"
            )

        return _run_sync(self._fetch_cloud_replay(task_id))

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def run_task_async(
        self,
        url: str,
        task: str,
        credentials: Optional[Dict[str, str]] = None,
        output_schema: Optional[Dict[str, str]] = None,
        max_steps: int = 50,
        timeout_seconds: int = 300,
        retry_attempts: int = 3,
        retry_delay_seconds: int = 2,
        max_cost_cents: Optional[int] = None,
    ) -> TaskResult:
        """Run a browser automation task asynchronously.

        This is the primary implementation.  :meth:`run_task` delegates here.

        Args:
            url: Starting URL the browser navigates to.
            task: Plain-English description of what the agent should do.
            credentials: Optional login credentials.
            output_schema: Declares structured fields to extract.
            max_steps: Maximum browser actions (default 50).
            timeout_seconds: Wall-clock timeout (default 300).
            retry_attempts: Retries on recoverable failures (default 3).
            retry_delay_seconds: Base delay between retries (default 2).
            max_cost_cents: Optional cost limit for the task.

        Returns:
            A :class:`TaskResult` with outcome, extracted data, and metadata.

        Raises:
            ValueError: If ``url`` is empty, ``task`` exceeds 2000 chars,
                or ``output_schema`` contains unsupported types.
        """
        if not url or not url.strip():
            raise ValueError("url must not be empty")
        if len(task) > 2000:
            raise ValueError("task must be 2000 characters or fewer")

        # Deferred API-key check: raise on first LLM call, not __init__.
        # Checked here (before the retry handler) because EnvironmentError
        # is an alias for OSError, which the retry handler treats as retryable.
        if self.local and not settings.ANTHROPIC_API_KEY:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Set it in your environment "
                "or .env file before running tasks in local mode."
            )

        config = TaskConfig(
            url=url,
            task=task,
            credentials=credentials,
            output_schema=output_schema,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_delay_seconds=retry_delay_seconds,
            max_cost_cents=max_cost_cents,
        )

        result = await self._dispatch(config)
        self._cache_result(result)

        # Best-effort reporting to Pokant dashboard
        if self.local and self.pokant_api_url and self.pokant_api_key:
            try:
                from computeruse._reporting import report_to_api

                await report_to_api(
                    api_url=self.pokant_api_url,
                    api_key=self.pokant_api_key,
                    task_id=result.task_id,
                    task_description=config.task,
                    status=result.status,
                    steps=result.step_data,
                    cost_cents=result.cost_cents,
                    error_category=result.error_category,
                    error_message=result.error,
                    duration_ms=result.duration_ms,
                    created_at=result.created_at,
                    analysis=result.analysis,
                    url=str(config.url) if config.url else "",
                    result=result.result if isinstance(result.result, dict) else ({"text": result.result} if result.result else None),
                )
                logger.info("Reported task %s to Pokant API", result.task_id)
            except Exception:
                logger.debug("Pokant API reporting failed (best-effort)", exc_info=True)

        return result

    # ------------------------------------------------------------------
    # Private: async execution layer
    # ------------------------------------------------------------------

    async def _dispatch(self, config: TaskConfig) -> TaskResult:
        """Dispatch *config* through the retry handler to the appropriate backend."""
        handler = RetryHandler(
            max_attempts=config.retry_attempts,
            base_delay=float(config.retry_delay_seconds),
        )

        if self.local:
            return await handler.execute_with_retry(self._local_execute, config)
        return await handler.execute_with_retry(self._call_cloud_api, config)

    async def _local_execute(self, config: TaskConfig) -> TaskResult:
        """Instantiate a fresh :class:`TaskExecutor` and run *config* locally.

        A new executor is created on every call so that per-run state
        is always clean, making concurrent ``run_task`` calls safe.
        """
        from computeruse.executor import TaskExecutor

        executor = TaskExecutor(
            model=self.model,
            headless=self.headless,
            browserbase_api_key=self.browserbase_api_key,
        )
        return await executor.execute(config)

    async def _call_cloud_api(self, config: TaskConfig) -> TaskResult:
        """Submit *config* to the hosted cloud service and poll for completion.

        On transient network errors during polling, retries up to
        ``_MAX_POLL_RETRIES`` times with ``_POLL_ERROR_BACKOFF`` seconds
        between attempts.

        Raises:
            APIError:           On any non-2xx HTTP response.
            NetworkError:       If polling fails after all retries.
            TaskExecutionError: If the task does not finish within the timeout.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "url": config.url,
            "task": config.task,
            "max_steps": config.max_steps,
            "timeout_seconds": config.timeout_seconds,
            "retry_attempts": config.retry_attempts,
            "retry_delay_seconds": config.retry_delay_seconds,
        }
        if config.credentials:
            payload["credentials"] = config.credentials
        if config.output_schema:
            payload["output_schema"] = config.output_schema
        if config.max_cost_cents is not None:
            payload["max_cost_cents"] = config.max_cost_cents
        if config.session_id:
            payload["session_id"] = config.session_id
        if config.idempotency_key:
            payload["idempotency_key"] = config.idempotency_key
        if config.webhook_url:
            payload["webhook_url"] = config.webhook_url

        async with httpx.AsyncClient(timeout=30) as client:
            submit = await client.post(f"{self._cloud_base}/tasks", headers=headers, json=payload)
            _raise_for_status(submit)
            task_id: str = submit.json()["task_id"]
            logger.info("Cloud task submitted: %s", task_id)

            deadline = time.monotonic() + _CLOUD_POLL_TIMEOUT
            poll_errors = 0

            while time.monotonic() < deadline:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    poll = await client.get(f"{self._cloud_base}/tasks/{task_id}", headers=headers)
                    _raise_for_status(poll)
                    poll_errors = 0  # reset on success
                    poll_data: Dict[str, Any] = poll.json()
                    status: str = poll_data.get("status", "")
                    logger.debug("Cloud task %s → %s", task_id, status)

                    if status in ("completed", "failed", "timeout", "cancelled"):
                        return _parse_cloud_result(poll_data)

                except (httpx.NetworkError, httpx.TimeoutException) as exc:
                    poll_errors += 1
                    if poll_errors >= _MAX_POLL_RETRIES:
                        raise NetworkError(
                            f"Lost connection to cloud API after " f"{_MAX_POLL_RETRIES} poll retry attempts: {exc}"
                        ) from exc
                    logger.warning(
                        "Poll error %d/%d: %s. Retrying in %.0fs...",
                        poll_errors,
                        _MAX_POLL_RETRIES,
                        exc,
                        _POLL_ERROR_BACKOFF,
                    )
                    await asyncio.sleep(_POLL_ERROR_BACKOFF)

        raise TaskExecutionError(
            f"Cloud task {task_id!r} did not reach a terminal state within "
            f"{_CLOUD_POLL_TIMEOUT}s.  "
            "Use get_task(task_id) to check the result later."
        )

    async def _fetch_cloud_task(self, task_id: str) -> TaskResult:
        """Fetch one task from the cloud API by ID.

        Raises:
            KeyError:  On HTTP 404.
            APIError:  On other non-2xx responses.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self._cloud_base}/tasks/{task_id}", headers=headers)
        if response.status_code == 404:
            raise KeyError(f"Cloud task {task_id!r} not found")
        _raise_for_status(response)
        return _parse_cloud_result(response.json())

    async def _list_cloud_tasks(self, limit: int, status: Optional[str] = None) -> List[TaskResult]:
        """Fetch recent tasks from the cloud API.

        Raises:
            APIError: If the HTTP request fails.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self._cloud_base}/tasks",
                headers=headers,
                params=params,
            )
        _raise_for_status(response)
        items: List[Dict[str, Any]] = response.json().get("tasks", [])
        return [_parse_cloud_result(item) for item in items]

    async def _fetch_cloud_replay(self, task_id: str) -> str:
        """Fetch the replay URL for a cloud task.

        Raises:
            ComputerUseSDKError: On HTTP 404.
            NetworkError: On connection failure.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self._cloud_base}/tasks/{task_id}/replay",
                    headers=headers,
                )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NetworkError(f"Failed to fetch replay for task {task_id!r}: {exc}") from exc

        if response.status_code == 404:
            raise ComputerUseSDKError(f"No replay found for cloud task {task_id!r}")
        _raise_for_status(response)
        return response.json().get("replay_url", "")

    # ------------------------------------------------------------------
    # Private: local task cache
    # ------------------------------------------------------------------

    def _cache_result(self, result: TaskResult) -> None:
        """Write *result* to ``.tasks/<task_id>.json``."""
        path = _TASK_STORE / f"{result.task_id}.json"
        try:
            path.write_text(result.to_json(indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not cache task result %s: %s", result.task_id, exc)

    def _load_cached_result(self, task_id: str) -> Optional[TaskResult]:
        """Load a cached :class:`TaskResult` by ID, or return ``None``."""
        path = _TASK_STORE / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            return TaskResult.from_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Corrupt cache file %s: %s", path, exc)
            return None

    def _list_cached_results(self, limit: int) -> List[TaskResult]:
        """Return up to *limit* cached results sorted by file mtime (newest first)."""
        files = sorted(
            _TASK_STORE.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        results: List[TaskResult] = []
        for path in files[:limit]:
            try:
                results.append(TaskResult.from_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("Skipping corrupt cache file %s: %s", path, exc)
        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _run_sync(coro: Any) -> Any:
    """Execute an async coroutine from synchronous code.

    Handles two scenarios:

    * **No running event loop** (normal scripts, CLIs) — calls
      :func:`asyncio.run` directly.
    * **Loop already running** (Jupyter, async frameworks) — spawns a
      :class:`~concurrent.futures.ThreadPoolExecutor` worker that runs its
      own :func:`asyncio.run` and blocks the calling thread via
      ``future.result()``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop — straightforward path.
        return asyncio.run(coro)

    # Loop is already running (Jupyter / FastAPI background task / etc.).
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _raise_for_status(response: httpx.Response) -> None:
    """Raise :class:`APIError` for any non-2xx *response*."""
    if response.is_success:
        return
    try:
        body: Any = response.json()
    except Exception:
        body = response.text
    raise APIError(
        message=f"HTTP {response.status_code} from {response.url}",
        status_code=response.status_code,
        response=body if isinstance(body, dict) else {"raw": body},
    )


def _parse_cloud_result(data: Dict[str, Any]) -> TaskResult:
    """Convert a raw cloud API response dict into a :class:`TaskResult`."""

    def _dt(val: Optional[str]) -> Optional[datetime]:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None

    created_at = _dt(data.get("created_at")) or datetime.now(timezone.utc)

    return TaskResult(
        task_id=data["task_id"],
        status=data.get("status", "failed"),
        success=data.get("success", False),
        result=data.get("result"),
        error=data.get("error"),
        replay_url=data.get("replay_url"),
        replay_path=data.get("replay_path"),
        steps=data.get("steps", 0),
        duration_ms=data.get("duration_ms", 0),
        created_at=created_at,
        completed_at=_dt(data.get("completed_at")),
    )
