"""
tests/unit/test_ingest.py — Tests for POST /api/v1/tasks/ingest endpoint.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_db, get_redis
from api.middleware.auth import get_current_account


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_ACCOUNT_ID = uuid.uuid4()


def _make_account(**overrides):
    defaults = dict(
        id=TEST_ACCOUNT_ID,
        email="test@pokant.dev",
        name="Test Account",
        tier="free",
        monthly_step_limit=500,
        monthly_steps_used=0,
        encryption_key_id="enc-key-1",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    acct = MagicMock()
    for k, v in defaults.items():
        setattr(acct, k, v)
    return acct


def _make_task(account_id: uuid.UUID, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        account_id=account_id,
        status="completed",
        success=True,
        url="https://example.com",
        task_description="SDK task",
        output_schema=None,
        result=None,
        error_code=None,
        error_message=None,
        model_used=None,
        total_steps=0,
        duration_ms=0,
        replay_s3_key=None,
        session_id=None,
        idempotency_key=None,
        webhook_url=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=datetime.now(timezone.utc),
        retry_count=0,
        retry_of_task_id=None,
        error_category=None,
        cost_cents=0,
        total_tokens_in=0,
        total_tokens_out=0,
        executor_mode="sdk",
        analysis_json=None,
        compiled_workflow_json=None,
        playwright_script=None,
    )
    defaults.update(overrides)
    task = MagicMock()
    for k, v in defaults.items():
        setattr(task, k, v)
    return task


@pytest.fixture
def test_account():
    return _make_account()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    return redis


@pytest.fixture
def client(test_account, mock_db, mock_redis):
    async def override_auth():
        return test_account

    async def override_db():
        yield mock_db

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_current_account] = override_auth
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = override_redis

    # Patch Celery (imported by module) and R2 client
    with (
        patch("api.routes.tasks._celery") as mock_celery,
        patch("api.routes.tasks.validate_url_async", new_callable=AsyncMock),
        patch("api.routes.tasks.validate_webhook_url", new_callable=AsyncMock),
    ):
        mock_celery.send_task = MagicMock()
        yield TestClient(app)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_no_duplicate(mock_db):
    """Configure mock_db so the duplicate-check SELECT returns None."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()


def _setup_duplicate_exists(mock_db):
    """Configure mock_db so the duplicate-check SELECT returns an existing task."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = _make_task(TEST_ACCOUNT_ID)
    mock_db.execute = AsyncMock(return_value=mock_result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestTask:
    def test_basic_ingest_returns_201(self, client, mock_db):
        """POST valid payload -> 201, task appears with correct fields."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "url": "https://example.com"},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "completed"
        assert body["executor_mode"] == "sdk"
        assert "task_id" in body

    def test_ingest_with_steps(self, client, mock_db):
        """POST payload with steps -> Task + TaskStep rows created."""
        _setup_no_duplicate(mock_db)

        steps = [
            {"step_number": 0, "action_type": "navigate", "description": "goto example.com"},
            {"step_number": 1, "action_type": "click", "description": "click login"},
            {"step_number": 2, "action_type": "extract", "description": "read result"},
        ]

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "steps": steps},
        )

        assert resp.status_code == 201
        # 1 Task + 3 TaskSteps = 4 db.add calls
        assert mock_db.add.call_count == 4

    def test_ingest_requires_auth(self):
        """POST without API key header -> 401."""
        no_auth_client = TestClient(app)
        resp = no_auth_client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed"},
        )
        assert resp.status_code == 401

    def test_duplicate_task_id_returns_409(self, client, mock_db):
        """POST same task_id twice -> 409 on second."""
        _setup_duplicate_exists(mock_db)

        task_id = str(uuid.uuid4())
        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"task_id": task_id, "status": "completed"},
        )

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error_code"] == "DUPLICATE_TASK"

    def test_ingested_task_visible_in_list(self, client, mock_db, test_account):
        """Ingested task appears in GET /api/v1/tasks list."""
        # First, ingest a task
        _setup_no_duplicate(mock_db)
        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "executor_mode": "sdk"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Now mock the list endpoint to return the ingested task
        ingested_task = _make_task(test_account.id, id=uuid.UUID(task_id), executor_mode="sdk")

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        mock_list_result = MagicMock()
        mock_list_result.scalars.return_value.all.return_value = [ingested_task]

        mock_db.execute = AsyncMock(side_effect=[mock_count_result, mock_list_result])

        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["executor_mode"] == "sdk"

    def test_steps_endpoint_returns_ingested_steps(self, client, mock_db, test_account):
        """GET /tasks/{id}/steps returns steps created via ingest."""
        task_id = uuid.uuid4()
        ingested_task = _make_task(test_account.id, id=task_id)

        step = MagicMock()
        step.step_number = 0
        step.action_type = "navigate"
        step.description = "goto example.com"
        step.screenshot_s3_key = None
        step.llm_tokens_in = 100
        step.llm_tokens_out = 50
        step.duration_ms = 1200
        step.success = True
        step.error_message = None
        step.created_at = datetime.now(timezone.utc)
        step.context = None

        # First call: task ownership check, second call: steps query
        mock_task_result = MagicMock()
        mock_task_result.scalar_one_or_none.return_value = ingested_task

        mock_steps_result = MagicMock()
        mock_steps_result.scalars.return_value.all.return_value = [step]

        mock_db.execute = AsyncMock(side_effect=[mock_task_result, mock_steps_result])

        resp = client.get(f"/api/v1/tasks/{task_id}/steps")
        assert resp.status_code == 200
        steps = resp.json()
        assert len(steps) == 1
        assert steps[0]["action_type"] == "navigate"
        assert steps[0]["tokens_in"] == 100

    def test_minimal_payload_defaults(self, client, mock_db):
        """POST with empty body -> 201 with sensible defaults."""
        _setup_no_duplicate(mock_db)

        resp = client.post("/api/v1/tasks/ingest", json={})

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "completed"
        assert body["executor_mode"] == "sdk"
        assert body["steps"] == 0
        assert body["cost_cents"] == 0.0

    def test_screenshot_upload_to_r2(self, client, mock_db):
        """POST with screenshot_base64 -> R2 upload called, s3_key set."""
        _setup_no_duplicate(mock_db)

        # A tiny valid PNG (1x1 pixel)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        b64_png = base64.b64encode(png_bytes).decode()

        mock_r2_client = MagicMock()
        mock_r2_client.put_object = MagicMock()

        with patch("api.routes.tasks._get_r2_client", return_value=mock_r2_client):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {
                            "step_number": 1,
                            "action_type": "screenshot",
                            "screenshot_base64": b64_png,
                        }
                    ],
                },
            )

        assert resp.status_code == 201
        mock_r2_client.put_object.assert_called_once()
        call_kwargs = mock_r2_client.put_object.call_args
        assert "replays/" in call_kwargs.kwargs.get("Key", call_kwargs[1].get("Key", ""))

    def test_invalid_status_returns_422(self, client, mock_db):
        """POST with invalid status -> 422."""
        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "invalid_status"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestIngestEdgeCases:
    def test_non_uuid_task_id_accepted_with_generated_uuid(self, client, mock_db):
        """POST with human-readable task_id -> 201, UUID is generated."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"task_id": "my-test-run", "status": "completed"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # task_id in response should be a valid UUID (not the original string)
        import uuid
        uuid.UUID(data["task_id"])
        assert "my-test-run" in (data.get("task_description") or "")

    def test_failed_status_sets_success_false(self, client, mock_db):
        """Ingesting a failed task sets success=False."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "failed",
                "error_message": "Element not found",
                "error_category": "permanent:element_not_found",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "failed"
        assert body["success"] is False
        assert body["error"] == "Element not found"
        assert body["error_category"] == "permanent:element_not_found"

    def test_timeout_status_accepted(self, client, mock_db):
        """The 'timeout' status is valid per DB CHECK constraint."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "timeout", "duration_ms": 120000},
        )

        assert resp.status_code == 201
        assert resp.json()["status"] == "timeout"

    def test_cancelled_status_accepted(self, client, mock_db):
        """The 'cancelled' status is valid per DB CHECK constraint."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "cancelled"},
        )

        assert resp.status_code == 201
        assert resp.json()["status"] == "cancelled"

    def test_custom_timestamps_parsed(self, client, mock_db):
        """ISO timestamps in created_at/completed_at are parsed correctly."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "completed",
                "created_at": "2026-03-15T10:30:00+00:00",
                "completed_at": "2026-03-15T10:31:05+00:00",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert "2026-03-15" in body["created_at"]
        assert "2026-03-15" in body["completed_at"]

    def test_invalid_timestamps_fallback_to_now(self, client, mock_db):
        """Garbage timestamps don't crash — fall back to now()."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "completed",
                "created_at": "not-a-date",
                "completed_at": "also-garbage",
            },
        )

        assert resp.status_code == 201

    def test_invalid_base64_screenshot_skipped(self, client, mock_db):
        """Invalid base64 in screenshot doesn't crash — just skips."""
        _setup_no_duplicate(mock_db)

        mock_r2_client = MagicMock()
        with patch("api.routes.tasks._get_r2_client", return_value=mock_r2_client):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {
                            "step_number": 0,
                            "action_type": "navigate",
                            "screenshot_base64": "!!!not-valid-base64!!!",
                        }
                    ],
                },
            )

        assert resp.status_code == 201
        # R2 put_object should NOT have been called since decode failed
        mock_r2_client.put_object.assert_not_called()

    def test_r2_not_configured_skips_screenshot(self, client, mock_db):
        """When R2 is not configured, screenshots are silently skipped."""
        _setup_no_duplicate(mock_db)

        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64_png = base64.b64encode(png_bytes).decode()

        # _get_r2_client returns None when R2 is unconfigured
        with patch("api.routes.tasks._get_r2_client", return_value=None):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {
                            "step_number": 0,
                            "action_type": "navigate",
                            "screenshot_base64": b64_png,
                        }
                    ],
                },
            )

        assert resp.status_code == 201

    def test_r2_upload_error_skips_screenshot(self, client, mock_db):
        """If R2 upload throws, screenshot is skipped (no crash)."""
        _setup_no_duplicate(mock_db)

        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64_png = base64.b64encode(png_bytes).decode()

        mock_r2_client = MagicMock()
        mock_r2_client.put_object.side_effect = Exception("R2 connection refused")

        with patch("api.routes.tasks._get_r2_client", return_value=mock_r2_client):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {
                            "step_number": 0,
                            "action_type": "navigate",
                            "screenshot_base64": b64_png,
                        }
                    ],
                },
            )

        assert resp.status_code == 201

    def test_long_description_truncated(self, client, mock_db):
        """Step descriptions over 500 chars are truncated."""
        _setup_no_duplicate(mock_db)

        long_desc = "x" * 1000

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "completed",
                "steps": [
                    {"step_number": 0, "action_type": "extract", "description": long_desc}
                ],
            },
        )

        assert resp.status_code == 201
        # Verify the TaskStep was added with truncated description
        add_calls = mock_db.add.call_args_list
        # Second add call is the TaskStep (first is Task)
        task_step_obj = add_calls[1][0][0]
        assert len(task_step_obj.description) == 500

    def test_cost_cents_decimal_precision(self, client, mock_db):
        """Float cost_cents is converted to Decimal without precision loss."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "cost_cents": 12.3456},
        )

        assert resp.status_code == 201
        # Check the Task object passed to db.add
        task_obj = mock_db.add.call_args_list[0][0][0]
        from decimal import Decimal
        assert task_obj.cost_cents == Decimal("12.3456")

    def test_explicit_task_id_used(self, client, mock_db):
        """When task_id is provided, it's used instead of generating one."""
        _setup_no_duplicate(mock_db)

        explicit_id = str(uuid.uuid4())
        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"task_id": explicit_id, "status": "completed"},
        )

        assert resp.status_code == 201
        assert resp.json()["task_id"] == explicit_id

    def test_task_description_fallback_to_url(self, client, mock_db):
        """Empty task_description falls back to URL."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "url": "https://example.com/page"},
        )

        assert resp.status_code == 201
        task_obj = mock_db.add.call_args_list[0][0][0]
        assert task_obj.task_description == "https://example.com/page"

    def test_task_description_fallback_to_sdk_task(self, client, mock_db):
        """Empty task_description and empty URL falls back to 'SDK task'."""
        _setup_no_duplicate(mock_db)

        resp = client.post("/api/v1/tasks/ingest", json={"status": "completed"})

        assert resp.status_code == 201
        task_obj = mock_db.add.call_args_list[0][0][0]
        assert task_obj.task_description == "SDK task"

    def test_many_steps(self, client, mock_db):
        """Ingesting a task with many steps works correctly."""
        _setup_no_duplicate(mock_db)

        steps = [
            {"step_number": i, "action_type": "navigate", "description": f"step {i}"}
            for i in range(50)
        ]

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "steps": steps},
        )

        assert resp.status_code == 201
        assert resp.json()["steps"] == 50
        # 1 Task + 50 TaskSteps = 51 db.add calls
        assert mock_db.add.call_count == 51

    def test_step_with_error_fields(self, client, mock_db):
        """Steps with success=False and error messages are stored correctly."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "failed",
                "error_message": "Task failed at step 2",
                "steps": [
                    {"step_number": 0, "action_type": "navigate", "success": True},
                    {"step_number": 1, "action_type": "click", "success": True},
                    {
                        "step_number": 2,
                        "action_type": "click",
                        "success": False,
                        "error": "Element not visible",
                    },
                ],
            },
        )

        assert resp.status_code == 201
        # Check the failed step
        failed_step = mock_db.add.call_args_list[3][0][0]  # 4th add: 3rd step
        assert failed_step.success is False
        assert failed_step.error_message == "Element not visible"

    def test_token_counts_passed_through(self, client, mock_db):
        """Token counts in steps are stored on TaskStep rows."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "completed",
                "total_tokens_in": 5000,
                "total_tokens_out": 1500,
                "steps": [
                    {"step_number": 0, "action_type": "navigate", "tokens_in": 2000, "tokens_out": 700},
                    {"step_number": 1, "action_type": "extract", "tokens_in": 3000, "tokens_out": 800},
                ],
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["total_tokens_in"] == 5000
        assert body["total_tokens_out"] == 1500

        # Verify step-level tokens
        step_0 = mock_db.add.call_args_list[1][0][0]
        assert step_0.llm_tokens_in == 2000
        assert step_0.llm_tokens_out == 700

    def test_no_celery_enqueue(self, client, mock_db):
        """Ingest endpoint does NOT enqueue any Celery task."""
        _setup_no_duplicate(mock_db)

        with patch("api.routes.tasks._celery") as mock_celery:
            mock_celery.send_task = MagicMock()
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={"status": "completed"},
            )

        assert resp.status_code == 201
        mock_celery.send_task.assert_not_called()

    def test_multiple_screenshots_uploaded(self, client, mock_db):
        """Multiple steps with screenshots each get their own R2 upload."""
        _setup_no_duplicate(mock_db)

        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64_png = base64.b64encode(png_bytes).decode()

        mock_r2_client = MagicMock()
        mock_r2_client.put_object = MagicMock()

        with patch("api.routes.tasks._get_r2_client", return_value=mock_r2_client):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {"step_number": 0, "action_type": "navigate", "screenshot_base64": b64_png},
                        {"step_number": 1, "action_type": "click", "screenshot_base64": b64_png},
                        {"step_number": 2, "action_type": "extract"},  # no screenshot
                    ],
                },
            )

        assert resp.status_code == 201
        assert mock_r2_client.put_object.call_count == 2

        # Verify S3 keys follow the convention
        keys = [call.kwargs["Key"] for call in mock_r2_client.put_object.call_args_list]
        task_id = resp.json()["task_id"]
        assert f"replays/{task_id}/step_0.png" in keys
        assert f"replays/{task_id}/step_1.png" in keys

    def test_executor_mode_custom_value(self, client, mock_db):
        """executor_mode passes through whatever the SDK sends."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "executor_mode": "sdk"},
        )

        assert resp.status_code == 201
        assert resp.json()["executor_mode"] == "sdk"

    def test_compiled_workflow_stored(self, client, mock_db):
        """POST with compiled_workflow -> stored on Task row."""
        _setup_no_duplicate(mock_db)

        workflow = {
            "name": "login_flow",
            "steps": [
                {
                    "action_type": "goto",
                    "selectors": [],
                    "fill_value_template": "",
                    "intent": "Navigate to login",
                    "timeout_ms": 2000,
                    "pre_url": "https://example.com/login",
                }
            ],
            "start_url": "https://example.com/login",
            "parameters": {},
            "source_task_id": "test-123",
            "compiled_at": "2026-04-03T12:00:00+00:00",
        }

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={"status": "completed", "compiled_workflow": workflow},
        )

        assert resp.status_code == 201
        task_obj = mock_db.add.call_args_list[0][0][0]
        assert task_obj.compiled_workflow_json is not None
        assert task_obj.compiled_workflow_json["name"] == "login_flow"
        assert len(task_obj.compiled_workflow_json["steps"]) == 1

    def test_compiled_workflow_in_response(self, client, mock_db, test_account):
        """GET /tasks/{id} returns compiled_workflow when present."""
        task_id = uuid.uuid4()
        workflow_data = {
            "name": "test_workflow",
            "steps": [{"action_type": "click", "intent": "Click button"}],
            "start_url": "https://example.com",
            "parameters": {"email": ""},
            "source_task_id": str(task_id),
            "compiled_at": "2026-04-03T12:00:00+00:00",
        }
        task_mock = _make_task(
            test_account.id,
            id=task_id,
            compiled_workflow_json=workflow_data,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task_mock
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["compiled_workflow"] is not None
        assert body["compiled_workflow"]["name"] == "test_workflow"
        assert body["compiled_workflow"]["parameters"] == {"email": ""}

    def test_compiled_workflow_null_when_absent(self, client, mock_db, test_account):
        """GET /tasks/{id} returns null compiled_workflow when not compiled."""
        task_id = uuid.uuid4()
        task_mock = _make_task(test_account.id, id=task_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task_mock
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["compiled_workflow"] is None


# ---------------------------------------------------------------------------
# End-to-end flow tests (ingest → read-back via other endpoints)
# ---------------------------------------------------------------------------

class TestIngestE2EFlows:
    """Test full flows: ingest a task, then verify it via GET endpoints."""

    def test_ingest_then_get_task(self, client, mock_db, test_account):
        """Ingest → GET /tasks/{id} → fields match."""
        _setup_no_duplicate(mock_db)

        ingest_payload = {
            "status": "completed",
            "url": "https://example.com/login",
            "task_description": "Log in and scrape data",
            "cost_cents": 3.14,
            "duration_ms": 45000,
            "total_tokens_in": 8000,
            "total_tokens_out": 2500,
            "executor_mode": "sdk",
            "steps": [
                {"step_number": 0, "action_type": "navigate", "description": "goto login page"},
            ],
        }

        resp = client.post("/api/v1/tasks/ingest", json=ingest_payload)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Mock DB to return the task for GET
        task_mock = _make_task(
            test_account.id,
            id=uuid.UUID(task_id),
            status="completed",
            success=True,
            url="https://example.com/login",
            task_description="Log in and scrape data",
            cost_cents=3.14,
            duration_ms=45000,
            total_tokens_in=8000,
            total_tokens_out=2500,
            executor_mode="sdk",
            total_steps=1,
        )

        mock_get_result = MagicMock()
        mock_get_result.scalar_one_or_none.return_value = task_mock
        mock_db.execute = AsyncMock(return_value=mock_get_result)

        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task_id
        assert body["status"] == "completed"
        assert body["url"] == "https://example.com/login"
        assert body["executor_mode"] == "sdk"
        assert body["duration_ms"] == 45000
        assert body["total_tokens_in"] == 8000
        assert body["total_tokens_out"] == 2500
        assert body["steps"] == 1

    def test_ingest_then_get_steps_with_screenshots(self, client, mock_db, test_account):
        """Ingest with screenshots → GET steps → screenshot_url populated."""
        _setup_no_duplicate(mock_db)

        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64_png = base64.b64encode(png_bytes).decode()

        mock_r2_client = MagicMock()
        mock_r2_client.put_object = MagicMock()

        with patch("api.routes.tasks._get_r2_client", return_value=mock_r2_client):
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={
                    "status": "completed",
                    "steps": [
                        {"step_number": 0, "action_type": "navigate", "screenshot_base64": b64_png},
                        {"step_number": 1, "action_type": "click"},
                    ],
                },
            )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Mock steps GET: step 0 has s3_key, step 1 doesn't
        task_mock = _make_task(test_account.id, id=uuid.UUID(task_id))

        step_0 = MagicMock()
        step_0.step_number = 0
        step_0.action_type = "navigate"
        step_0.description = None
        step_0.screenshot_s3_key = f"replays/{task_id}/step_0.png"
        step_0.llm_tokens_in = 0
        step_0.llm_tokens_out = 0
        step_0.duration_ms = 0
        step_0.success = True
        step_0.error_message = None
        step_0.created_at = datetime.now(timezone.utc)
        step_0.context = None

        step_1 = MagicMock()
        step_1.step_number = 1
        step_1.action_type = "click"
        step_1.description = None
        step_1.screenshot_s3_key = None
        step_1.llm_tokens_in = 0
        step_1.llm_tokens_out = 0
        step_1.duration_ms = 0
        step_1.success = True
        step_1.error_message = None
        step_1.created_at = datetime.now(timezone.utc)
        step_1.context = None

        mock_task_result = MagicMock()
        mock_task_result.scalar_one_or_none.return_value = task_mock
        mock_steps_result = MagicMock()
        mock_steps_result.scalars.return_value.all.return_value = [step_0, step_1]
        mock_db.execute = AsyncMock(side_effect=[mock_task_result, mock_steps_result])

        # Mock presign_screenshot to return a URL
        with patch("api.routes.tasks.presign_screenshot", return_value="https://r2.example.com/signed"):
            resp = client.get(f"/api/v1/tasks/{task_id}/steps")

        assert resp.status_code == 200
        steps = resp.json()
        assert len(steps) == 2
        # Step 0 has screenshot
        assert steps[0]["screenshot_url"] == "https://r2.example.com/signed"
        # Step 1 has no screenshot
        assert steps[1]["screenshot_url"] is None

    def test_ingest_mixed_with_cloud_tasks_in_list(self, client, mock_db, test_account):
        """SDK-ingested and cloud tasks coexist in the task list."""
        cloud_task = _make_task(
            test_account.id,
            status="completed",
            executor_mode="browser_use",
        )
        sdk_task = _make_task(
            test_account.id,
            status="completed",
            executor_mode="sdk",
        )

        mock_count = MagicMock()
        mock_count.scalar.return_value = 2
        mock_list = MagicMock()
        mock_list.scalars.return_value.all.return_value = [sdk_task, cloud_task]
        mock_db.execute = AsyncMock(side_effect=[mock_count, mock_list])

        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 2
        modes = {t["executor_mode"] for t in tasks}
        assert modes == {"sdk", "browser_use"}

    def test_ingest_failed_task_then_verify_error_fields(self, client, mock_db, test_account):
        """Ingest a failed task → GET it → error fields are correct."""
        _setup_no_duplicate(mock_db)

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "failed",
                "error_message": "Timeout waiting for selector #login-btn",
                "error_category": "transient:timeout",
                "duration_ms": 120000,
                "steps": [
                    {"step_number": 0, "action_type": "navigate", "success": True, "duration_ms": 3000},
                    {
                        "step_number": 1,
                        "action_type": "click",
                        "success": False,
                        "error": "Timeout waiting for selector #login-btn",
                        "duration_ms": 117000,
                    },
                ],
            },
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Mock GET for the failed task
        failed_task = _make_task(
            test_account.id,
            id=uuid.UUID(task_id),
            status="failed",
            success=False,
            error_message="Timeout waiting for selector #login-btn",
            error_category="transient:timeout",
            duration_ms=120000,
            total_steps=2,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = failed_task
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["success"] is False
        assert body["error"] == "Timeout waiting for selector #login-btn"
        assert body["error_category"] == "transient:timeout"
        assert body["duration_ms"] == 120000

    def test_ingest_does_not_increment_quota(self, client, mock_db, test_account):
        """monthly_steps_used is NOT modified by ingest."""
        _setup_no_duplicate(mock_db)
        original_usage = test_account.monthly_steps_used

        resp = client.post(
            "/api/v1/tasks/ingest",
            json={
                "status": "completed",
                "steps": [
                    {"step_number": 0, "action_type": "navigate"},
                    {"step_number": 1, "action_type": "click"},
                    {"step_number": 2, "action_type": "extract"},
                ],
            },
        )

        assert resp.status_code == 201
        assert test_account.monthly_steps_used == original_usage

    def test_ingest_with_all_valid_statuses(self, client, mock_db):
        """Every status in the DB CHECK constraint is accepted."""
        valid_statuses = ["queued", "running", "completed", "failed", "timeout", "cancelled"]

        for s in valid_statuses:
            _setup_no_duplicate(mock_db)
            resp = client.post(
                "/api/v1/tasks/ingest",
                json={"status": s},
            )
            assert resp.status_code == 201, f"Status '{s}' should be accepted but got {resp.status_code}"
