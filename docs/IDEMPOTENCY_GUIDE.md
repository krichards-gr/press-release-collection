# Idempotency & Daily Scheduling Guide

## Overview

The pipeline uses **three layers of deduplication** (checked in cheapest-first order)
to ensure it is safe to run on any schedule, will never double-charge for SERP API
calls, and will never insert duplicate rows into BigQuery.

| Layer | What it checks | When it fires | Cost saved |
|-------|---------------|---------------|------------|
| 1. Auto start_date | Last completed run's `end_date` from `collection_runs` | Every run with no explicit date | BigQuery query cost |
| 2. Query-level dedup | Exact SERP query strings executed in past runs | Before any SERP API call | SERP API credits |
| 3. URL-level dedup | `press_release_id` (MD5 url) already in `press_release_metadata` | After SERP returns results | Scraping cost + BQ storage |

---

## Daily Scheduling (No Parameters)

Send an empty POST to trigger a fully automatic daily run:

```bash
curl -X POST $SERVICE_URL
```

**What happens internally**:
1. Query `collection_runs` → find last completed run's `end_date`
2. Set `start_date = last_end_date` (1-day overlap keeps safety buffer)
3. Set `end_date = today`
4. Run normally with dedup layers 2 & 3

**First-ever run** (no history in `collection_runs`):
- Falls back to `start_date = today - 7 days`

**Example timeline**:
```
Run 1 (2026-03-01): start=2026-02-22, end=2026-03-01  → collects 7 days
Run 2 (2026-03-02): start=2026-03-01, end=2026-03-02  → collects new day
Run 3 (2026-03-03): start=2026-03-02, end=2026-03-03  → collects new day
...
```

---

## Deduplication Layers In Detail

### Layer 1: Auto start_date (date range detection)

BigQuery query in `get_last_successful_run_end_date()`:
```sql
SELECT FORMAT_DATE('%Y-%m-%d', end_date) AS end_date_str
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
  AND end_timestamp IS NOT NULL
ORDER BY end_timestamp DESC
LIMIT 1
```

The result becomes `start_date` for the new run. This means the next run always
picks up exactly where the last one left off (with a 1-day overlap for safety).

### Layer 2: Query-level deduplication (SERP API cost savings)

Before executing any SERP queries, the pipeline checks which exact query strings
were already executed for overlapping date ranges:

```sql
SELECT DISTINCT query
FROM `pressure_monitoring.collection_runs`,
UNNEST(queries_executed) AS query
WHERE status = 'completed'
  AND start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
  AND start_date <= DATE('{end_date}')
  AND end_date >= DATE('{start_date}')
```

Any query in this set is skipped — zero SERP API calls for it.

**Saves money when**: re-running the same date range, or when layer 1
auto-detects a start_date that partially overlaps a previous run.

### Layer 3: URL-level deduplication (prevents duplicate BQ rows)

After SERP results come in, each URL is hashed: `press_release_id = MD5(url)`.
The pipeline loads all existing IDs from BigQuery and filters them out:

```python
existing_ids = storage.get_existing_press_release_ids()
# SELECT press_release_id FROM press_release_metadata

serp_df = serp_df[~serp_df['press_release_id'].isin(existing_ids)]
```

This is the final safety net — even if a URL appears in results from a new
query, it won't be scraped or stored again.

---

## Scenarios

### Scenario 1: Normal daily run

```bash
# Cloud Scheduler fires at 2 AM with no body
curl -X POST $SERVICE_URL
```

Timeline for a run on 2026-03-04:
1. Auto-detect: last run ended 2026-03-03 → `start=2026-03-03, end=2026-03-04`
2. Generate 100 queries for 2026-03-03 → 2026-03-04
3. Query dedup: yesterday's queries already logged → skip 0 (different date → new queries)
4. SERP: collects results
5. URL dedup: removes any articles already in BQ from previous overlap
6. Scrape + write new content

**Cost**: Only new articles (from 2026-03-04) are scraped and stored.

---

### Scenario 2: Re-running the same date range (idempotency test)

```bash
curl -X POST $SERVICE_URL \
  -d '{"start_date": "2026-03-01", "end_date": "2026-03-03"}'
# Run a second time with the same body
curl -X POST $SERVICE_URL \
  -d '{"start_date": "2026-03-01", "end_date": "2026-03-03"}'
```

Second run result:
- Layer 2: All 100 queries already in `queries_executed` → **0 SERP API calls**
- Layer 3: All URLs already in metadata → nothing written to BQ
- Cost: ~$0 (only BigQuery read cost for the dedup checks)

---

### Scenario 3: New company added to reference data

Add "NewCorp Inc." to the reference BigQuery table, then run normally:

```bash
curl -X POST $SERVICE_URL
```

What happens:
1. Auto-detect last run date
2. `get_collected_newsroom_urls()` finds NewCorp's newsroom URL is not in
   `press_release_metadata.newsroom_url` → marks it as needing backfill
3. Extends `effective_start_date = "2026-01-01"` for NewCorp's queries
4. Existing companies: their queries for the recent window are already in
   `queries_executed` → skipped (layer 2)
5. NewCorp queries execute; URL dedup (layer 3) prevents any overlap with
   existing articles

**Cost**: Only pay SERP API for NewCorp's historical queries.

---

### Scenario 4: Force refresh (bypass deduplication)

```bash
curl -X POST $SERVICE_URL \
  -d '{"start_date": "2026-03-01", "end_date": "2026-03-03", "force_refresh": true}'
```

- All dedup layers are bypassed
- All queries are re-executed; all URLs are re-scraped
- ⚠️ Creates duplicate rows in BigQuery — use sparingly

---

## Monitoring Idempotency

```sql
-- Check a specific date range was collected exactly once
SELECT COUNT(*) AS run_count
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
  AND start_date <= '2026-03-03'
  AND end_date >= '2026-03-01';

-- Verify no duplicate press releases
SELECT press_release_id, COUNT(*) AS n
FROM `pressure_monitoring.press_release_metadata`
GROUP BY press_release_id
HAVING n > 1;

-- Check dedup effectiveness (large queries_skipped = money saved)
SELECT
  run_id, start_date, end_date,
  queries_count AS executed,
  urls_collected
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
ORDER BY start_timestamp DESC
LIMIT 10;
```

---

## Deduplication in the CLI (`main_cli.py`)

The CLI also uses BigQuery for incremental mode:

```bash
python main_cli.py --incremental
```

`find_last_run_date()` checks `collection_runs` first (so CLI incremental mode
aligns with Cloud Run history), then falls back to local checkpoint files.

The CLI uses `URLTracker` (file-based) for in-session URL deduplication —
this is supplementary to the BQ-based dedup above.

---

## Troubleshooting

### Issue: `press_release_id` duplicates appear

**Cause**: `force_refresh=true` was used, or `get_existing_press_release_ids()`
failed silently before a write.

**Fix**:
```sql
-- Deduplicate: keep earliest row per press_release_id
CREATE OR REPLACE TABLE `pressure_monitoring.press_release_metadata` AS
SELECT * EXCEPT(rn)
FROM (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY press_release_id ORDER BY collection_timestamp ASC) AS rn
  FROM `pressure_monitoring.press_release_metadata`
)
WHERE rn = 1;
```

### Issue: `--incremental` starts from wrong date

**Check**: Query `collection_runs` for the most recent completed run:
```sql
SELECT run_id, start_date, end_date, status, end_timestamp
FROM `pressure_monitoring.collection_runs`
ORDER BY start_timestamp DESC
LIMIT 5;
```

### Issue: Backfill not triggering for a new company

**Check**: Verify the company's `newsroom_url` in reference data matches what's
in `press_release_metadata.newsroom_url`:
```sql
SELECT DISTINCT newsroom_url
FROM `pressure_monitoring.press_release_metadata`
WHERE company = 'NewCorp Inc.'
LIMIT 5;
```

---

## Best Practices

1. **Daily trigger**: Send an empty POST — let BQ auto-detect the date range.
2. **Never use `force_refresh`** for scheduled runs (creates duplicates).
3. **Adding companies**: Just update reference data; next run auto-backfills.
4. **Monitor `collection_runs`**: Check `status = 'failed'` after each run.
5. **Let idempotency work**: Re-running any range is always safe and nearly free.
