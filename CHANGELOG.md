# Changelog

## 2026-02-11 - Query-Level Deduplication (SERP API Cost Optimization)

### Overview
Shifted deduplication to happen BEFORE SERP API calls instead of after, preventing unnecessary API costs when re-running the pipeline.

### Problem
- Original implementation deduplicated URLs AFTER collecting SERP results
- Re-running same date range still incurred full SERP API costs
- Only saved on scraping and BigQuery storage, not SERP collection

### Solution
**Query-Level Deduplication**:
- Track executed queries in `collection_runs.queries_executed`
- Before hitting SERP API, check if queries have been executed
- Only execute new queries
- **Result**: Re-running same date range = $0 SERP API costs

### Technical Changes

**BigQuery Schema Updates**:
```sql
-- Added to collection_runs table
queries_executed STRING REPEATED  -- Full query URLs executed
queries_count INTEGER             -- Count of queries executed
```

**New Methods**:
- `get_executed_queries_for_date_range()` - Returns set of executed query strings
- Updated `log_run_completion()` - Stores executed queries

**Pipeline Flow Changes**:

**Before**:
1. Generate queries (100)
2. Execute all queries via SERP API ($$$)
3. Deduplicate URLs (skip 80 already-collected)
4. Scrape 20 new URLs

**After**:
1. Generate queries (100)
2. **Check executed queries → 80 already executed**
3. **Execute only 20 new queries via SERP API** (80% cost savings!)
4. Scrape results

### Cost Impact

**Scenario**: Running same date range twice

**Before**:
- Run 1: 100 SERP queries ($$$) → collect 150 URLs
- Run 2: 100 SERP queries ($$$) → dedupe 150 URLs → $0 scraping
- Total: 200 SERP queries

**After**:
- Run 1: 100 SERP queries ($$$) → collect 150 URLs
- Run 2: Skip all 100 queries → $0 SERP, $0 scraping
- Total: 100 SERP queries (50% savings)

**Scenario**: Adding 1 new company (100 existing companies)

**Before**:
- 101 SERP queries (all companies) → most URLs already collected

**After**:
- Skip 100 queries (existing) → Execute 1 query (new company only)
- 99% SERP cost savings

### API Response Changes

**New Stats Fields**:
```json
{
  "stats": {
    "queries_generated": 100,   // Total queries generated
    "queries_skipped": 80,       // Queries skipped (already executed)
    "queries_executed": 20,      // Queries actually executed
    "serp_results_count": 30     // URLs collected
  }
}
```

### Files Modified
- `bigquery_storage.py` - Added queries tracking
- `main.py` - Query-level deduplication logic
- `BIGQUERY_SCHEMA.md` - Documented new fields
- `DEPLOYMENT.md` - Updated cost optimization section
- `IDEMPOTENCY_GUIDE.md` - Updated all examples

---

## 2026-02-11 - Idempotency & Automatic Backfill

### Overview
Added comprehensive idempotency and automatic backfill support to prevent duplicate data collection and automatically collect historical data for new URLs.

### New Features

#### 1. Collection Run Tracking
- **New Table**: `collection_runs` in BigQuery
- Logs every pipeline execution with metadata:
  - run_id, start_date, end_date
  - urls_collected, articles_scraped
  - status (started, completed, failed)
  - timestamps and error messages

#### 2. Idempotency (Duplicate Prevention)
- **Deduplication**: Before collecting, checks what URLs were already collected for the date range
- **Smart Skip**: Skips URLs already in BigQuery for the same date range
- **Cost Savings**: Prevents paying for duplicate BigQuery storage
- **Safe Re-runs**: Can run the same date range multiple times without duplicates

**How it works**:
1. Pipeline checks `collected_articles` for URLs in date range
2. Filters out already-collected URLs from SERP results
3. Only processes new URLs

#### 3. Automatic Backfill
- **Detection**: Identifies URLs (companies) not yet in BigQuery
- **Auto-Backfill**: New URLs automatically get data from **2026-01-01** to present
- **Seamless**: No manual intervention required

**How it works**:
1. Pipeline compares reference data URLs vs. collected URLs
2. For new URLs: sets start_date = "2026-01-01"
3. For existing URLs: uses requested start_date
4. Generates queries for appropriate date ranges

#### 4. Force Refresh Option
- **Bypass**: `force_refresh: true` skips deduplication
- **Use Case**: Re-scrape data when content has changed
- **Warning**: Creates duplicates - use sparingly

### API Changes

**New Response Fields**:
```json
{
  "stats": {
    "backfill_urls_count": 5,      // New URLs needing backfill
    "deduplicated_urls": 120,       // URLs skipped (already collected)
    "serp_results_count": 30        // New URLs to process
  }
}
```

### Database Schema Changes

**New Table**: `collection_runs`
```sql
CREATE TABLE pressure_monitoring.collection_runs (
  run_id STRING NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  companies_processed ARRAY<STRING>,
  urls_collected INT64,
  articles_scraped INT64,
  status STRING NOT NULL,
  start_timestamp TIMESTAMP NOT NULL,
  end_timestamp TIMESTAMP,
  error_message STRING
)
PARTITION BY DATE(start_timestamp)
```

### New Methods in BigQueryStorage

1. **`log_run_start()`** - Log pipeline start
2. **`log_run_completion()`** - Log pipeline completion/failure
3. **`get_collected_urls_for_date_range()`** - Check what's collected (idempotency)
4. **`get_all_collected_urls()`** - Get all collected URLs
5. **`identify_urls_needing_backfill()`** - Find new URLs needing historical data

### Files Modified

- **bigquery_storage.py**
  - Added `create_collection_runs_table()`
  - Added 5 new methods for tracking and deduplication
  - Updated `initialize_tables()` to create collection_runs

- **main.py**
  - Updated `run_serp_collection()` with backfill and deduplication logic
  - Added run logging (start/completion/failure)
  - Updated error handling to log failures

- **BIGQUERY_SCHEMA.md**
  - Documented collection_runs table
  - Added example queries for idempotency checks

- **DEPLOYMENT.md**
  - Added "Pipeline Features" section
  - Documented idempotency, backfill, and force_refresh

### Cost Optimization Impact

**Before**: Running the same date range 3 times = 3x storage costs
**After**: Running the same date range 3 times = 1x storage costs (deduplicated)

**Before**: Adding new company = manual backfill from 2026-01-01
**After**: Adding new company = automatic backfill on next run

### Testing

```bash
# Test idempotency - run same date range twice
curl -X POST $SERVICE_URL -H "Content-Type: application/json" -d '{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07"
}'

# Second run should show: "deduplicated_urls": N (all URLs)
curl -X POST $SERVICE_URL -H "Content-Type: application/json" -d '{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07"
}'

# Test backfill - add new company to reference data, then run
curl -X POST $SERVICE_URL -H "Content-Type: application/json" -d '{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07"
}'
# Should show: "backfill_urls_count": 1 and collect from 2026-01-01

# Test force refresh - bypass deduplication
curl -X POST $SERVICE_URL -H "Content-Type: application/json" -d '{
  "start_date": "2026-02-01",
  "end_date": "2026-02-07",
  "force_refresh": true
}'
# Should show: "deduplicated_urls": 0 (nothing skipped)
```

---

## 2026-02-11 - Duplicate Column Fix

### Issue
When merging SERP results with scraped article content, duplicate columns (title_x, title_y) were created because both datasets contained 'title' fields.

### Solution
Modified all scraper functions to return only content-specific fields, deferring to SERP data for title and description:

**Scraper Output Fields (Before)**:
- title (DUPLICATE with SERP)
- url
- summary
- publish_date
- keywords
- article_text
- scraper_used

**Scraper Output Fields (After)**:
- url (merge key)
- summary
- publish_date
- keywords
- article_text
- scraper_used

**SERP Fields (Unchanged)**:
- url (merge key)
- title
- description
- rank
- query

**Final Merged Output** (No Duplicates):
- url
- title (from SERP)
- description (from SERP)
- rank (from SERP)
- query (from SERP)
- summary (from scraper)
- publish_date (from scraper)
- keywords (from scraper)
- article_text (from scraper)
- scraper_used (from scraper)

### Files Modified
- `article_scraper.py`
  - Removed 'title' from all four scraper functions:
    - scrape_with_newspaper()
    - scrape_with_trafilatura()
    - scrape_with_readability()
    - scrape_with_goose()
  - Updated docstrings to clarify SERP precedence
  - Added comment at merge operation explaining the design decision

### Impact
- ✅ No duplicate columns in f100_joined.csv
- ✅ No duplicate columns in BigQuery collected_articles table
- ✅ SERP title and description always take precedence
- ✅ Clean schema matches BIGQUERY_SCHEMA.md documentation

### Testing
To verify the fix works:
```bash
# Run the pipeline
python article_scraper.py

# Check the output CSV for duplicate columns
head -n 1 outputs/f100_joined.csv | tr ',' '\n' | sort | uniq -d
# Should return nothing (no duplicates)

# Verify column names
head -n 1 outputs/f100_joined.csv
# Should show: url,title,description,rank,query,summary,publish_date,keywords,article_text,scraper_used
```
