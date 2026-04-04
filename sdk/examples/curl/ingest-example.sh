#!/bin/bash
# Example: Report a completed browser agent run to Pokant via curl.
# Works with any agent in any language — just POST JSON.
#
# Usage:
#   bash ingest-example.sh
#   POKANT_API_URL=https://api.pokant.dev POKANT_API_KEY=cu_live_... bash ingest-example.sh

API_URL="${POKANT_API_URL:-http://localhost:8000}"
API_KEY="${POKANT_API_KEY:-cu_test_testkey1234567890abcdef12}"

echo "Posting to ${API_URL}/api/v1/tasks/ingest ..."
echo ""

curl -s -X POST "${API_URL}/api/v1/tasks/ingest" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "task_description": "Example: extract page title from example.com",
    "status": "completed",
    "executor_mode": "sdk",
    "duration_ms": 5000,
    "steps": [
      {
        "step_number": 0,
        "action_type": "navigate",
        "description": "goto(https://example.com)",
        "duration_ms": 2000,
        "success": true
      },
      {
        "step_number": 1,
        "action_type": "extract",
        "description": "Extracted page title: Example Domain",
        "duration_ms": 3000,
        "success": true
      }
    ]
  }' | python3 -m json.tool

DASHBOARD_URL="${POKANT_DASHBOARD_URL:-http://localhost:3000}"
echo ""
echo "View in dashboard: ${DASHBOARD_URL}/tasks"
