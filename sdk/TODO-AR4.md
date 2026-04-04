# AR4: Dashboard Retry Intelligence Visualization

## Status: COMPLETE

## Tasks

- [x] Part 1: Verify GET /api/runs/{task_id} returns attempts data as-is
- [x] Part 2: Add retry timeline CSS to index.html
- [x] Part 2: Add renderRetryTimeline() JS function
- [x] Part 2: Integrate retry timeline into renderDetail()
- [x] Part 3: Add adaptive_retry_stats to GET /api/stats in dashboard.py
- [x] Part 3: Add renderRetryStatsSection() and integrate into renderStats()
- [x] Verification: Create test fixture and visual check

## Changes

### dashboard.py
- Added adaptive retry stats computation to `GET /api/stats` endpoint
- New fields: `runs_retried`, `avg_attempts`, `total_diagnosis_cost_cents`, `category_counts`

### static/index.html
- Added CSS for retry timeline components (attempt cards, env tags, summaries)
- Added `renderRetryTimeline(run)` — horizontal attempt cards with diagnosis, strategy, env changes, cost
- Added `renderRetryStatsSection(stats)` — retry intelligence stats in Stats tab
- Integrated retry timeline into `renderDetail()` above step timeline
- Integrated retry stats into `renderStats()` below error categories
