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
from computeruse.exceptions import APIError, TaskExecutionError
from computeruse.executor import TaskExecutor
from computeruse.models import TaskConfig, TaskResult
from computeruse.retry import RetryHandler

logger = logging.getLogger(__name__)

# Directory where completed TaskResult objects are cached as JSON files.
_TASK_STORE = Path(".tasks")

# Hosted cloud API base URL.
_CLOUD_API_BASE = "https://api.computeruse.dev/v1"

# Seconds between cloud-task status polls.
_POLL_INTERVAL: float = 2.0

# Hard ceiling (seconds) for cloud-task polling before giving up.
_CLOUD_POLL_TIMEOUT: int = 600


class ComputerUse:
    """One-line entry point for browser automation powered by Claude.

    :class:`ComputerUse` wraps the full SDK pipeline behind a single
    synchronous :meth:`run_task` call so it works in ordinary scripts,
    Jupyter notebooks, and CLIs without any event-loop boilerplate.

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

    Data extraction with typed schema::

        result = cu.run_task(
            url="https://finance.yahoo.com/quote/AAPL",
            task="Get the current stock price and today's change percentage",
            output_schema={
                "price":      "float",
                "change_pct": "float",
                "currency":   "str",
            },
        )
        data = result.result
        print(f"AAPL: {data['currency']}{data['price']}  ({data['change_pct']:+.2f}%)")

    Authenticated workflow (session is cached after first login)::

        result = cu.run_task(
            url="https://github.com/login",
            task="Star the repo anthropics/anthropic-sdk-python",
            credentials={"username": "alice", "password": "s3cr3t"},
        )

    Accessing all TaskResult fields::

        if result.success:
            print(result.result)          # extracted data dict
            print(result.steps)           # number of browser actions
            print(result.duration_ms)     # wall-clock time in ms
            print(result.replay_path)     # local replay JSON path
            print(result.task_id)         # unique run identifier
        else:
            print(result.error)           # human-readable failure reason
            print(result.replay_path)     # replay still written on failure

    Attributes:
        model (str): Anthropic model ID used for this client instance.
            Readable after construction, e.g. ``print(cu.model)``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        local: bool = True,
        model: str = settings.DEFAULT_MODEL,
        headless: bool = True,
        browserbase_api_key: Optional[str] = None,
    ) -> None:
        """Initialise the ComputerUse client.

        Args:
            api_key:
                API key for the hosted *computeruse* cloud service.
                Only required when ``local=False``.  Not the same as
                ``ANTHROPIC_API_KEY`` — that is read from the environment
                automatically.
            local:
                ``True`` (default) — run tasks in a local Playwright browser.
                ``False`` — dispatch tasks to the cloud API; requires
                ``api_key``.
            model:
                Anthropic model ID for the Browser Use agent and structured
                output extraction.  Defaults to ``settings.DEFAULT_MODEL``
                (``"claude-sonnet-4-5"``).  Override per-client::

                    cu = ComputerUse(model="claude-opus-4-5")
                    print(cu.model)   # "claude-opus-4-5"

            headless:
                Run the browser without a visible window.  Set to ``False``
                while developing to watch the agent work::

                    cu = ComputerUse(headless=False)

                Ignored in cloud mode.
            browserbase_api_key:
                BrowserBase API key for managed remote browsers in local
                mode.  Falls back to ``settings.BROWSERBASE_API_KEY`` when
                ``None``.

        Raises:
            ValueError: If ``local=False`` and no ``api_key`` is provided.
        """
        self.api_key = api_key
        self.local = local
        self.model = model          # publicly documented attribute
        self.headless = headless
        self.browserbase_api_key = browserbase_api_key or settings.BROWSERBASE_API_KEY

        if not local and not api_key:
            raise ValueError(
                "An 'api_key' is required for cloud execution (local=False). "
                "Pass it as ComputerUse(api_key='cu-…', local=False) or set "
                "COMPUTERUSE_API_KEY in your environment."
            )

        _TASK_STORE.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public synchronous API
    # ------------------------------------------------------------------

    def run_task(
        self,
        url: str,
        task: str,
        credentials: Optional[Dict[str, str]] = None,
        output_schema: Optional[Dict[str, str]] = None,
        max_steps: int = settings.DEFAULT_MAX_STEPS,
        timeout_seconds: int = settings.DEFAULT_TIMEOUT,
        retry_attempts: int = 3,
        retry_delay_seconds: int = 2,
    ) -> TaskResult:
        """Run a browser automation task and block until it completes.

        Constructs a :class:`TaskConfig`, dispatches execution (locally or to
        the cloud), caches the :class:`TaskResult` to ``.tasks/``, and
        returns it.  All parameters except ``url`` and ``task`` are optional.

        Args:
            url:
                Starting URL the browser should navigate to before the agent
                starts working.  Must be a valid HTTPS (or HTTP) URL::

                    url="https://news.ycombinator.com"

            task:
                Plain-English description of what the agent should do.
                Be specific — include any page-interaction details::

                    task="Click the 'Sign in' button and log in with the
                          provided credentials, then navigate to Settings."

            credentials:
                Optional ``{"key": "value"}`` mapping of login credentials.
                Common keys: ``"username"``, ``"password"``, ``"email"``.
                When provided, the browser session is saved after completion
                and automatically restored on the next call for the same
                domain — the agent skips the login form entirely::

                    credentials={"username": "alice", "password": "hunter2"}

            output_schema:
                Declares the structured fields to extract from the page and
                their types.  Supported type strings:

                * Scalars:        ``"str"``, ``"int"``, ``"float"``, ``"bool"``
                * Collections:    ``"list"``, ``"dict"``
                * Parameterised:  ``"list[str]"``, ``"list[int]"``,
                                  ``"dict[str, int]"``, ``"dict[str, float]"``
                * Nested:         ``"list[dict[str, str]]"``

                Example::

                    output_schema={
                        "price":  "float",
                        "tags":   "list[str]",
                        "meta":   "dict[str, str]",
                    }

            max_steps:
                Maximum number of browser actions (clicks, types, navigations)
                the agent may take before the run is forcibly terminated.
                Defaults to ``settings.DEFAULT_MAX_STEPS`` (50).

            timeout_seconds:
                Wall-clock timeout for the entire task in seconds.  The task
                is terminated with :exc:`TimeoutError` if it exceeds this
                limit regardless of how many steps have been taken.
                Defaults to ``settings.DEFAULT_TIMEOUT`` (300).

            retry_attempts:
                Number of additional attempts on recoverable failures
                (network errors, HTTP 429/5xx).  ``0`` means no retries.

            retry_delay_seconds:
                Base delay in seconds between retry attempts.  The actual
                delay grows exponentially: ``delay = base * 2 ** attempt``,
                capped at 30 s.

        Returns:
            A :class:`TaskResult` with the following fields:

            * ``success`` (``bool``) — ``True`` if the task completed without error.
            * ``result`` (``dict | None``) — extracted data matching
              ``output_schema``; ``None`` when no schema was provided.
            * ``error`` (``str | None``) — failure description when ``success=False``.
            * ``steps`` (``int``) — total browser actions taken.
            * ``duration_ms`` (``int``) — wall-clock duration in milliseconds.
            * ``replay_path`` (``str | None``) — local path to the replay JSON.
            * ``replay_url`` (``str | None``) — hosted replay URL (cloud mode).
            * ``task_id`` (``str``) — unique UUID for this run.
            * ``status`` (``str``) — ``"completed"`` or ``"failed"``.
            * ``created_at`` / ``completed_at`` (``datetime``) — UTC timestamps.

        Raises:
            ValidationError:    If ``output_schema`` contains an invalid type
                                string (caught at ``TaskConfig`` construction).
            ComputerUseError:   Base class for any SDK-level failure.

        Examples::

            cu = ComputerUse()

            # Stock price extraction
            r = cu.run_task(
                url="https://finance.yahoo.com/quote/AAPL",
                task="Get the current stock price",
                output_schema={"price": "float", "currency": "str"},
            )
            if r.success:
                print(r.result["price"])

            # Form submission with error handling
            r = cu.run_task(
                url="https://example.com/contact",
                task="Fill and submit the contact form with name 'Alice'",
                output_schema={"submitted": "bool", "message": "str"},
                max_steps=20,
            )
            if not r.success:
                print(f"Failed after {r.steps} steps: {r.error}")
                print(f"Replay: {r.replay_path}")
        """
        config = TaskConfig(
            url=url,
            task=task,
            credentials=credentials,
            output_schema=output_schema,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )

        result = _run_sync(self._run_task_async(config))
        self._cache_result(result)
        return result

    def get_task(self, task_id: str) -> TaskResult:
        """Retrieve a previously executed task by its ID.

        Checks the local ``.tasks/`` cache first; falls back to the cloud API
        when ``local=False`` and the task is not cached locally.

        Args:
            task_id:
                The ``task_id`` value from a previous :class:`TaskResult`::

                    result = cu.run_task(…)
                    same   = cu.get_task(result.task_id)

        Returns:
            The matching :class:`TaskResult`.

        Raises:
            KeyError:  If the task is not found locally or in the cloud.
            APIError:  If the cloud API request fails with a non-404 error.
        """
        cached = self._load_cached_result(task_id)
        if cached is not None:
            return cached

        if not self.local:
            return _run_sync(self._fetch_cloud_task(task_id))

        raise KeyError(
            f"No task with id {task_id!r} found in local storage ({_TASK_STORE})"
        )

    def list_tasks(self, limit: int = 10) -> List[TaskResult]:
        """Return the most recent task results, newest first.

        In local mode reads from the ``.tasks/`` cache sorted by file
        modification time.  In cloud mode fetches from the API
        (``GET /api/v1/tasks?limit=…``).

        Args:
            limit: Maximum number of results to return.  Must be ≥ 1.

        Returns:
            List of :class:`TaskResult` objects ordered newest-first.
            May be shorter than *limit* if fewer tasks exist.

        Example::

            for task in cu.list_tasks(limit=5):
                print(task.task_id, task.status, task.duration_ms)
        """
        if self.local:
            return self._list_cached_results(limit)
        return _run_sync(self._list_cloud_tasks(limit))

    # ------------------------------------------------------------------
    # Private: async execution layer
    # ------------------------------------------------------------------

    async def _run_task_async(self, config: TaskConfig) -> TaskResult:
        """Async core of :meth:`run_task`.

        Wraps the chosen backend (local executor or cloud API) in a
        :class:`RetryHandler` configured from *config*.

        Args:
            config: Fully validated :class:`TaskConfig`.

        Returns:
            :class:`TaskResult` from the backend.
        """
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
        (``steps`` list, screenshot directory) is always clean, making
        concurrent ``run_task`` calls safe.

        Args:
            config: Task configuration.

        Returns:
            :class:`TaskResult` from :meth:`TaskExecutor.execute`.
        """
        executor = TaskExecutor(
            model=self.model,
            headless=self.headless,
            browserbase_api_key=self.browserbase_api_key,
        )
        return await executor.execute(config)

    async def _call_cloud_api(self, config: TaskConfig) -> TaskResult:
        """Submit *config* to the hosted cloud service and poll for completion.

        Flow:

        1. ``POST /api/v1/tasks`` — submits the task, receives a ``task_id``.
        2. ``GET  /api/v1/tasks/{task_id}`` — polled every
           :data:`_POLL_INTERVAL` seconds until status is ``"completed"`` or
           ``"failed"``, or :data:`_CLOUD_POLL_TIMEOUT` seconds elapse.

        Args:
            config: Task configuration to submit.

        Returns:
            :class:`TaskResult` built from the final poll response.

        Raises:
            APIError:           On any non-2xx HTTP response.
            TaskExecutionError: If the task does not finish within
                                :data:`_CLOUD_POLL_TIMEOUT` seconds.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "url":              config.url,
            "task":             config.task,
            "max_steps":        config.max_steps,
            "timeout_seconds":  config.timeout_seconds,
            "retry_attempts":   config.retry_attempts,
            "retry_delay_seconds": config.retry_delay_seconds,
        }
        if config.credentials:
            payload["credentials"] = config.credentials
        if config.output_schema:
            payload["output_schema"] = config.output_schema

        async with httpx.AsyncClient(timeout=30) as client:
            submit = await client.post(
                f"{_CLOUD_API_BASE}/tasks", headers=headers, json=payload
            )
            _raise_for_status(submit)
            task_id: str = submit.json()["task_id"]
            logger.info("Cloud task submitted: %s", task_id)

            deadline = time.monotonic() + _CLOUD_POLL_TIMEOUT
            while time.monotonic() < deadline:
                await asyncio.sleep(_POLL_INTERVAL)
                poll = await client.get(
                    f"{_CLOUD_API_BASE}/tasks/{task_id}", headers=headers
                )
                _raise_for_status(poll)
                poll_data: Dict[str, Any] = poll.json()
                status: str = poll_data.get("status", "")
                logger.debug("Cloud task %s → %s", task_id, status)

                if status in ("completed", "failed"):
                    return _parse_cloud_result(poll_data)

        raise TaskExecutionError(
            f"Cloud task {task_id!r} did not reach a terminal state within "
            f"{_CLOUD_POLL_TIMEOUT}s.  "
            "Use get_task(task_id) to check the result later."
        )

    async def _fetch_cloud_task(self, task_id: str) -> TaskResult:
        """Fetch one task from the cloud API by ID.

        Args:
            task_id: Task to retrieve.

        Returns:
            :class:`TaskResult` from the API response.

        Raises:
            KeyError:  On HTTP 404.
            APIError:  On other non-2xx responses.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{_CLOUD_API_BASE}/tasks/{task_id}", headers=headers
            )
        if response.status_code == 404:
            raise KeyError(f"Cloud task {task_id!r} not found")
        _raise_for_status(response)
        return _parse_cloud_result(response.json())

    async def _list_cloud_tasks(self, limit: int) -> List[TaskResult]:
        """Fetch recent tasks from the cloud API.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of :class:`TaskResult`, newest-first.

        Raises:
            APIError: If the HTTP request fails.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{_CLOUD_API_BASE}/tasks",
                headers=headers,
                params={"limit": limit},
            )
        _raise_for_status(response)
        items: List[Dict[str, Any]] = response.json().get("tasks", [])
        return [_parse_cloud_result(item) for item in items]

    # ------------------------------------------------------------------
    # Private: local task cache
    # ------------------------------------------------------------------

    def _cache_result(self, result: TaskResult) -> None:
        """Write *result* to ``.tasks/<task_id>.json``.

        Failures are logged as warnings — a cache write error must never
        surface to the caller.
        """
        path = _TASK_STORE / f"{result.task_id}.json"
        try:
            path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not cache task result %s: %s", result.task_id, exc)

    def _load_cached_result(self, task_id: str) -> Optional[TaskResult]:
        """Load a cached :class:`TaskResult` by ID, or return ``None``."""
        path = _TASK_STORE / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            return TaskResult.model_validate_json(path.read_text(encoding="utf-8"))
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
                results.append(
                    TaskResult.model_validate_json(path.read_text(encoding="utf-8"))
                )
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
      ``future.result()``.  This avoids the ``RuntimeError: This event
      loop is already running`` that a direct ``asyncio.run()`` would raise.

    Args:
        coro: Awaitable coroutine to run.

    Returns:
        Whatever the coroutine returns.
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
    """Raise :class:`APIError` for any non-2xx *response*.

    Includes the parsed JSON body (or raw text) in the exception so callers
    get actionable detail without having to re-read the response.

    Args:
        response: :class:`httpx.Response` to inspect.

    Raises:
        APIError: If ``response.is_success`` is ``False``.
    """
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
    """Convert a raw cloud API response dict into a :class:`TaskResult`.

    Parses ISO-8601 timestamp strings into :class:`datetime` objects and
    provides safe defaults for every optional field so the function never
    raises even when the API returns a partial response.

    Args:
        data: Decoded JSON body from a ``GET /api/v1/tasks/{id}`` response.

    Returns:
        A fully populated :class:`TaskResult`.
    """
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
