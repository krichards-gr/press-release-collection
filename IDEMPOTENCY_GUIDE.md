# Idempotency & Backfill Guide

## Quick Reference

### What is Idempotency?

**Problem**: Running the same date range multiple times wastes money on SERP API calls and creates duplicate BigQuery data.
**Solution**: Pipeline checks what queries have been executed and skips them BEFORE hitting the SERP API.
**Result**:
- ✅ Safe to re-run (no duplicates)
- 💰 Zero SERP API costs on re-runs
- 💰 Zero BigQuery storage costs on re-runs

### What is Automatic Backfill?

**Problem**: Adding a new company means manually collecting historical data from 2026-01-01.
**Solution**: Pipeline detects new URLs and automatically backfills from 2026-01-01.
**Result**: Add company → run pipeline → complete historical data automatically.

---

## How It Works

### 1. Normal Run (No New Companies)

**Request**:
```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07"
}
```

**What Happens**:
1. ✅ Check for new URLs → None found
2. ✅ Generate 100 queries for 2026-02-01 to 2026-02-07
3. 💰 **Check already-executed queries** → All 100 executed previously
4. 💰 **Skip SERP API entirely** (0 queries, $0 cost)
5. ✅ Log completion (nothing to collect)

**Response**:
```json
{
  "status": "success",
  "stats": {
    "backfill_urls_count": 0,
    "queries_generated": 100,
    "queries_skipped": 100,
    "queries_executed": 0,
    "serp_results_count": 0,
    "articles_scraped": 0
  }
}
```

**Cost Impact**: Second run = $0 (no SERP API calls, no scraping)

### 2. Run with New Company Added

**Scenario**: You add "NewCorp Inc" to reference data
**Request**:
```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07"
}
```

**What Happens**:
1. ✅ Check for new URLs → Found 1 new (NewCorp)
2. 🔄 **Extend date range: 2026-01-01 to 2026-02-07** (backfill!)
3. ✅ Generate 138 queries:
   - NewCorp: 38 queries (2026-01-01 to 2026-02-07)
   - Existing 100 companies: 100 queries (2026-02-01 to 2026-02-07)
4. 💰 **Check already-executed queries** → 100 already executed
5. 💰 **Skip 100 queries, execute 38** (only NewCorp queries)
6. ✅ Collect SERP results for NewCorp only
7. ✅ Scrape NewCorp's articles (historical + recent)
8. ✅ Write to BigQuery

**Response**:
```json
{
  "status": "success",
  "stats": {
    "backfill_urls_count": 1,
    "queries_generated": 138,
    "queries_skipped": 100,
    "queries_executed": 38,
    "serp_results_count": 50,
    "articles_scraped": 50
  }
}
```

**Cost Impact**: Only pay SERP API costs for NewCorp (38 queries), existing companies free

### 3. Force Refresh (Bypass Deduplication)

**Use Case**: Content has changed, need to re-scrape
**Request**:
```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07",
  "force_refresh": true
}
```

**What Happens**:
1. ⚠️  Skip deduplication check
2. ✅ Collect all SERP results
3. ✅ Scrape all URLs (including already-collected ones)
4. ⚠️  Write to BigQuery (creates duplicates!)

**Warning**: This creates duplicate records. Use sparingly.

---

## BigQuery Tables

### collection_runs

**Purpose**: Track pipeline executions

**Query Recent Runs**:
```sql
SELECT
  run_id,
  start_date,
  end_date,
  status,
  urls_collected,
  articles_scraped,
  TIMESTAMP_DIFF(end_timestamp, start_timestamp, SECOND) as duration_sec
FROM `pressure_monitoring.collection_runs`
ORDER BY start_timestamp DESC
LIMIT 10;
```

**Check Idempotency**:
```sql
-- How many times have we collected 2026-02-01 to 2026-02-07?
SELECT COUNT(*) as runs
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
  AND start_date = '2026-02-01'
  AND end_date = '2026-02-07';
```

### collected_articles

**Check Duplicates** (should be zero):
```sql
SELECT url, COUNT(*) as duplicate_count
FROM `pressure_monitoring.collected_articles`
GROUP BY url
HAVING COUNT(*) > 1;
```

**Backfill Coverage**:
```sql
-- For a specific company, what date range do we have?
SELECT
  MIN(DATE(publish_date)) as earliest_article,
  MAX(DATE(publish_date)) as latest_article,
  COUNT(*) as total_articles
FROM `pressure_monitoring.collected_articles`
WHERE query LIKE '%NewCorp%'
  AND article_text IS NOT NULL;
```

---

## Cost Implications

### Before Idempotency

**Scenario**: Run same date range 5 times
**Cost**: 5x BigQuery storage + 5x scraping

### After Idempotency

**Scenario**: Run same date range 5 times
**Cost**: 1x BigQuery storage + 1x scraping (4 runs skipped)

### Backfill Cost

**Before**: Manual backfill = separate run from 2026-01-01
**After**: Automatic backfill = happens in next run (no extra API calls)

---

## Common Scenarios

### Scenario 1: Daily Scheduled Run

**Setup**: Cloud Scheduler runs daily at 2 AM
**Date Range**: Yesterday to today
**Behavior**:
- ✅ First run: Collects all data
- ✅ Second run (if re-run): Skips duplicates
- ✅ New company added: Auto-backfills from 2026-01-01

### Scenario 2: Weekly Full Refresh

**Setup**: Cloud Scheduler runs weekly
**Date Range**: Last 7 days
**Behavior**:
- ✅ Collects only new URLs from last 7 days
- ✅ Skips URLs already collected in previous daily runs
- ✅ Minimal duplicate processing

### Scenario 3: Historical Data Collection

**Setup**: Manually run for large date range
**Date Range**: 2026-01-01 to 2026-02-07
**Behavior**:
- ✅ First run: Collects everything
- ✅ Second run: Skips everything (fully idempotent)
- ✅ No duplicates, no wasted cost

### Scenario 4: Adding Multiple New Companies

**Setup**: Bulk add 10 new companies to reference data
**Next Run**: 2026-02-01 to 2026-02-07
**Behavior**:
- 🔄 Detects 10 new URLs
- 🔄 Extends date range to 2026-01-01 for these companies
- ✅ Backfills all 10 companies from 2026-01-01
- ✅ Existing companies: only 2026-02-01 to 2026-02-07
- ✅ Single run gets complete data for all companies

---

## Monitoring

### Check for Failed Runs
```sql
SELECT *
FROM `pressure_monitoring.collection_runs`
WHERE status = 'failed'
ORDER BY start_timestamp DESC;
```

### Check Deduplication Effectiveness
```sql
-- Compare URLs collected vs. URLs after dedup
SELECT
  run_id,
  urls_collected as total_found,
  articles_scraped as after_dedup,
  urls_collected - articles_scraped as duplicates_skipped
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
ORDER BY start_timestamp DESC
LIMIT 10;
```

### Identify Backfill Runs
```sql
SELECT *
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
  AND start_date = '2026-01-01'  -- Backfill runs start from 2026-01-01
ORDER BY start_timestamp DESC;
```

---

## Troubleshooting

### Issue: Duplicates in BigQuery

**Symptom**: Same URL appears multiple times
**Cause**: `force_refresh: true` was used
**Solution**:
```sql
-- Remove duplicates (keep earliest)
CREATE OR REPLACE TABLE `pressure_monitoring.collected_articles` AS
SELECT * EXCEPT(row_num)
FROM (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY url ORDER BY collection_timestamp ASC) as row_num
  FROM `pressure_monitoring.collected_articles`
)
WHERE row_num = 1;
```

### Issue: Backfill Not Happening

**Symptom**: New company added but no historical data
**Check**: Query collection_runs table
**Cause**: Company's pressroom_url not in reference data
**Solution**: Ensure reference data has correct pressroom_url column

### Issue: All URLs Skipped (0 Collected)

**Symptom**: `deduplicated_urls: 150, serp_results_count: 0`
**Cause**: Date range already fully collected
**Solution**: This is normal! Pipeline is working correctly (idempotency)
**Action**: Either:
  - Request a different date range
  - Use `force_refresh: true` if re-scraping is needed

---

## Best Practices

1. **Normal Operations**: Never use `force_refresh` for scheduled runs
2. **Adding Companies**: Just add to reference data, next run auto-backfills
3. **Re-scraping**: Only use `force_refresh` if content has genuinely changed
4. **Monitoring**: Regularly check `collection_runs` for failed runs
5. **Cost Control**: Idempotency saves money - let it work!

---

## Configuration

### Backfill Start Date

**Current**: Hardcoded to `2026-01-01` in main.py
**Location**: `run_serp_collection()` function
**To Change**:
```python
backfill_urls = storage.identify_urls_needing_backfill(
    current_urls=current_urls,
    backfill_start_date="2025-01-01"  # Change this
)
```

### Deduplication Window

**Current**: Checks entire history
**To Limit** (e.g., only last 30 days):
```python
already_collected = storage.get_collected_urls_for_date_range(
    start_date="2026-01-08",  # 30 days ago
    end_date=end_date
)
```
