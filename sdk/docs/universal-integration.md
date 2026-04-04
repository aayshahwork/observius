# Connect Any Agent to Pokant

Any agent in any language can push results to Pokant with a single HTTP POST to the ingest endpoint. No SDK required — just JSON over HTTP.

## Quick Start

```bash
curl -X POST http://localhost:8000/api/v1/tasks/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "task_description": "Extract pricing from vendor portal",
    "status": "completed",
    "executor_mode": "sdk",
    "duration_ms": 12500,
    "cost_cents": 3.45,
    "total_tokens_in": 1500,
    "total_tokens_out": 800,
    "steps": [
      {
        "step_number": 0,
        "action_type": "navigate",
        "description": "Opened vendor portal",
        "duration_ms": 2300,
        "success": true,
        "tokens_in": 0,
        "tokens_out": 0,
        "screenshot_base64": null
      },
      {
        "step_number": 1,
        "action_type": "extract",
        "description": "Extracted pricing table",
        "duration_ms": 5200,
        "success": true,
        "tokens_in": 1500,
        "tokens_out": 800,
        "screenshot_base64": null
      }
    ]
  }'
```

## Authentication

All requests require an API key in the `X-API-Key` header:

```
X-API-Key: cu_your_api_key_here
```

Get your API key from the Pokant dashboard at **Settings > API Keys**, or use the test key for local development:

```
cu_test_testkey1234567890abcdef12
```

## Endpoint

```
POST /api/v1/tasks/ingest
```

**Content-Type:** `application/json`

**Returns:** `201 Created` with a `TaskResponse` containing the assigned `task_id`.

## Request Body Reference

### Task Fields (`TaskIngestRequest`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `task_id` | `string` | No | auto-generated UUID | Custom task ID. Must be a valid UUID if provided. |
| `url` | `string` | No | `""` | Starting URL of the task. |
| `task_description` | `string` | No | `""` | Human-readable description of what the agent did. |
| `status` | `string` | No | `"completed"` | Final status. One of: `"completed"`, `"failed"`, `"timeout"`, `"cancelled"`, `"queued"`, `"running"`. |
| `cost_cents` | `float` | No | `0.0` | Total LLM cost in US cents. |
| `total_tokens_in` | `int` | No | `0` | Total input tokens consumed across all steps. |
| `total_tokens_out` | `int` | No | `0` | Total output tokens produced across all steps. |
| `error_category` | `string` | No | `null` | Error classification (e.g. `"timeout"`, `"auth"`, `"transient"`). Only for failed tasks. |
| `error_message` | `string` | No | `null` | Human-readable error description. Only for failed tasks. |
| `executor_mode` | `string` | No | `"sdk"` | Identifies the execution engine. Use `"sdk"` for custom agents. |
| `duration_ms` | `int` | No | `0` | Total task duration in milliseconds. |
| `steps` | `array` | No | `[]` | Array of step objects (see below). |
| `created_at` | `string` | No | current time | ISO 8601 timestamp of when the task started. |
| `completed_at` | `string` | No | current time | ISO 8601 timestamp of when the task finished. |

### Step Fields (`StepIngestData`)

Each object in the `steps` array:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `step_number` | `int` | **Yes** | — | Step index. Convention: start from 0. |
| `action_type` | `string` | No | `"unknown"` | Category of action: `"navigate"`, `"click"`, `"type"`, `"extract"`, `"scroll"`, `"wait"`, etc. |
| `description` | `string` | No | `""` | What happened in this step (max 500 chars stored). |
| `tokens_in` | `int` | No | `0` | Input tokens consumed by this step's LLM call. |
| `tokens_out` | `int` | No | `0` | Output tokens produced by this step's LLM call. |
| `duration_ms` | `int` | No | `0` | Step duration in milliseconds. |
| `success` | `bool` | No | `true` | Whether this step succeeded. |
| `error` | `string` | No | `null` | Error message if the step failed. |
| `screenshot_base64` | `string` | No | `null` | Base64-encoded PNG or JPEG screenshot, or `null` to skip. |

## Response Format

A successful `201 Created` response returns a `TaskResponse`:

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "url": "",
  "task_description": "Extract pricing from vendor portal",
  "status": "completed",
  "success": true,
  "result": null,
  "error": null,
  "replay_url": null,
  "steps": 2,
  "duration_ms": 12500,
  "created_at": "2026-04-01T12:00:00Z",
  "completed_at": "2026-04-01T12:00:12.500Z",
  "retry_count": 0,
  "retry_of_task_id": null,
  "error_category": null,
  "cost_cents": 3.45,
  "total_tokens_in": 1500,
  "total_tokens_out": 800,
  "executor_mode": "sdk"
}
```

Use the `task_id` from the response to query the task later:

```bash
# Get task details
curl http://localhost:8000/api/v1/tasks/{task_id} -H "X-API-Key: ..."

# Get step-level data with screenshot URLs
curl http://localhost:8000/api/v1/tasks/{task_id}/steps -H "X-API-Key: ..."
```

## Error Responses

| Status | Meaning |
|--------|---------|
| `401 Unauthorized` | Missing or invalid API key. |
| `409 Conflict` | A task with this `task_id` already exists. |
| `422 Unprocessable Entity` | Invalid `task_id` format or `status` value. |

## Screenshots

To include screenshots, base64-encode the image bytes and set `screenshot_base64` on each step:

```python
import base64
screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
```

Screenshots are uploaded to cloud storage (R2) and viewable in the dashboard replay. Pass `null` to skip screenshots for a step.

## Reporting Failed Tasks

For failed tasks, set `status` to `"failed"` and provide error details:

```json
{
  "task_description": "Extract pricing",
  "status": "failed",
  "error_message": "Navigation timeout after 30s",
  "error_category": "timeout",
  "duration_ms": 30000,
  "steps": [
    {
      "step_number": 0,
      "action_type": "navigate",
      "description": "Attempted to load vendor portal",
      "success": false,
      "error": "net::ERR_CONNECTION_TIMED_OUT"
    }
  ]
}
```

## Viewing Results

After ingesting a task, view it in the Pokant dashboard:

```
http://localhost:3000/tasks
```

Or query the API:

```bash
# List all tasks
curl http://localhost:8000/api/v1/tasks -H "X-API-Key: ..."

# Get a specific task
curl http://localhost:8000/api/v1/tasks/{task_id} -H "X-API-Key: ..."

# Get step details with screenshot URLs
curl http://localhost:8000/api/v1/tasks/{task_id}/steps -H "X-API-Key: ..."
```

## Language Examples

| Language | Example |
|----------|---------|
| Python | Use the native `PokantTracker` — see [SDK README](../README.md) |
| TypeScript | [`examples/typescript/pokant-reporter.ts`](../examples/typescript/pokant-reporter.ts) |
| TypeScript + Stagehand | [`examples/typescript/stagehand-with-pokant.ts`](../examples/typescript/stagehand-with-pokant.ts) |
| Go | [`examples/go/reporter.go`](../examples/go/reporter.go) |
| curl / shell | [`examples/curl/ingest-example.sh`](../examples/curl/ingest-example.sh) |

## Minimal Payload

The smallest valid request — just a status:

```json
{
  "status": "completed"
}
```

This creates a task with no steps, zero cost, and auto-generated timestamps. Useful for testing connectivity.
