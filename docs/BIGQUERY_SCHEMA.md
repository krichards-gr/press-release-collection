# BigQuery Schema Documentation

## Dataset: `pressure_monitoring`

3-table design — mirrors the metadata/content split used in the
`earnings-call-collector` project:

| Table | Analogue in earnings-call-collector | Purpose |
|-------|-------------------------------------|---------|
| `press_release_metadata` | `earnings_call_transcript_metadata` | One row per press release — SERP fields + company info |
| `press_release_content` | `earnings_call_transcript_content` | Scraped article text for each release |
| `collection_runs` | — | Pipeline run log — idempotency and scheduling |

> **Legacy**: `collected_articles` still exists in BigQuery with historical data
> from before the split. It is no longer written to by the pipeline.

---

## Table 1: `press_release_metadata`

**Purpose**: One row per press release. Immutable once collected.
Contains all SERP fields plus company context derived from reference data.

**Schema**:

| Column | Type | Mode | Description |
|--------|------|------|-------------|
| `press_release_id` | STRING | REQUIRED | MD5(url) — deterministic ID linking to content table |
| `url` | STRING | REQUIRED | Article URL |
| `title` | STRING | NULLABLE | Article title (scraped page title when available, otherwise SERP title) |
| `description` | STRING | NULLABLE | Meta description from SERP |
| `rank` | INTEGER | NULLABLE | Search result rank position |
| `query` | STRING | NULLABLE | Full Google search query URL that found this article |
| `company` | STRING | NULLABLE | Corporation name from reference data (e.g. "Apple Inc.") |
| `newsroom_url` | STRING | NULLABLE | Newsroom base URL used in `site:` search (e.g. "newsroom.apple.com") |
| `publish_date` | TIMESTAMP | NULLABLE | Article publication date (scraped where available) |
| `collection_timestamp` | TIMESTAMP | REQUIRED | When the article was collected |
| `run_id` | STRING | NULLABLE | Pipeline run identifier (YYYYMMDD_HHMMSS) |

**Partitioned** by `collection_timestamp` (day).
**Clustered** by `company`, `press_release_id`.

**Example Queries**:
```sql
-- All press releases for a specific company in the last 30 days
SELECT url, title, publish_date, collection_timestamp
FROM `pressure_monitoring.press_release_metadata`
WHERE company = 'Apple Inc.'
  AND collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
ORDER BY publish_date DESC;

-- Check for duplicates (should be zero — idempotent pipeline)
SELECT press_release_id, COUNT(*) as count
FROM `pressure_monitoring.press_release_metadata`
GROUP BY press_release_id
HAVING COUNT(*) > 1;
```

---

## Table 2: `press_release_content`

**Purpose**: Scraped article text for each press release.
One row per successfully scraped release. Joined to metadata via `press_release_id`.

**Schema**:

| Column | Type | Mode | Description |
|--------|------|------|-------------|
| `press_release_id` | STRING | REQUIRED | MD5(url) — foreign key to `press_release_metadata` |
| `article_text` | STRING | NULLABLE | Full article text extracted by scraper |
| `summary` | STRING | NULLABLE | Auto-generated summary (newspaper3k NLP) |
| `keywords` | STRING | NULLABLE | Extracted keywords, comma-separated (newspaper3k NLP) |
| `scraper_used` | STRING | NULLABLE | Which scraper successfully extracted content |
| `collection_timestamp` | TIMESTAMP | REQUIRED | When content was scraped |
| `run_id` | STRING | NULLABLE | Pipeline run identifier |

**Partitioned** by `collection_timestamp` (day).
**Clustered** by `press_release_id`.

**Example Queries**:
```sql
-- Full text for a specific press release
SELECT m.company, m.title, m.url, c.article_text
FROM `pressure_monitoring.press_release_metadata` m
JOIN `pressure_monitoring.press_release_content` c
  ON m.press_release_id = c.press_release_id
WHERE m.url = 'https://newsroom.apple.com/2026/03/01/some-release.html';

-- Coverage: how many releases have scraped content?
SELECT
  COUNT(DISTINCT m.press_release_id) AS total_releases,
  COUNT(DISTINCT c.press_release_id) AS with_content,
  ROUND(COUNT(DISTINCT c.press_release_id) / COUNT(DISTINCT m.press_release_id) * 100, 1)
    AS scrape_coverage_pct
FROM `pressure_monitoring.press_release_metadata` m
LEFT JOIN `pressure_monitoring.press_release_content` c
  ON m.press_release_id = c.press_release_id
WHERE m.collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
```

---

## Table 3: `collection_runs`

**Purpose**: Tracks every pipeline execution.
- **Scheduling**: `get_last_successful_run_end_date()` reads this table so the
  daily Cloud Run trigger needs no hardcoded dates — it automatically resumes
  from where the last successful run ended.
- **SERP cost savings**: Query strings executed per run are stored; the next
  run skips any query that was already executed for an overlapping date range.
- **Audit trail**: Full history of runs with timing, counts, and errors.

**Schema**:

| Column | Type | Mode | Description |
|--------|------|------|-------------|
| `run_id` | STRING | REQUIRED | Unique run identifier (YYYYMMDD_HHMMSS) |
| `start_date` | DATE | REQUIRED | Start of date range collected |
| `end_date` | DATE | REQUIRED | End of date range collected |
| `companies_processed` | STRING | REPEATED | Companies processed in this run |
| `queries_executed` | STRING | REPEATED | SERP query strings executed (for deduplication) |
| `queries_count` | INTEGER | NULLABLE | Number of queries executed |
| `urls_collected` | INTEGER | NULLABLE | New URLs collected |
| `articles_scraped` | INTEGER | NULLABLE | Articles successfully scraped |
| `status` | STRING | REQUIRED | `started` → `completed` or `failed` |
| `start_timestamp` | TIMESTAMP | REQUIRED | When run started |
| `end_timestamp` | TIMESTAMP | NULLABLE | When run completed/failed |
| `error_message` | STRING | NULLABLE | Error details if status = `failed` |

**Partitioned** by `start_timestamp` (day).

**Example Queries**:
```sql
-- Recent runs with timing
SELECT
  run_id,
  start_date,
  end_date,
  status,
  urls_collected,
  articles_scraped,
  TIMESTAMP_DIFF(end_timestamp, start_timestamp, SECOND) AS duration_sec
FROM `pressure_monitoring.collection_runs`
ORDER BY start_timestamp DESC
LIMIT 20;

-- What date will the next daily run start from?
SELECT FORMAT_DATE('%Y-%m-%d', end_date) AS next_run_start
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
ORDER BY end_timestamp DESC
LIMIT 1;

-- Failed runs
SELECT run_id, start_date, end_date, error_message
FROM `pressure_monitoring.collection_runs`
WHERE status = 'failed'
ORDER BY start_timestamp DESC;

-- SERP API savings: queries generated vs executed
SELECT
  run_id, start_date, end_date,
  queries_count AS queries_executed,
  urls_collected,
  ROUND(urls_collected / NULLIF(queries_count, 0), 2) AS avg_urls_per_query
FROM `pressure_monitoring.collection_runs`
WHERE status = 'completed'
ORDER BY start_timestamp DESC
LIMIT 10;
```

---

## Joining All Tables

**Full view: metadata + content**:

```sql
SELECT
  m.company,
  m.url,
  m.title,
  m.publish_date,
  m.newsroom_url,
  c.article_text,
  c.scraper_used
FROM `pressure_monitoring.press_release_metadata` m
LEFT JOIN `pressure_monitoring.press_release_content` c
  ON m.press_release_id = c.press_release_id
WHERE m.collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
ORDER BY m.company, m.publish_date DESC;
```

---

## Schema Management

Tables are auto-created by the pipeline on first run:

```python
from bigquery_storage import BigQueryStorage
storage = BigQueryStorage()
storage.initialize_tables()
# Creates: press_release_metadata, press_release_content, collection_runs
```

---

## Cost Optimization

```sql
-- ✅ GOOD: partition filter — scans only recent partition(s)
SELECT * FROM `pressure_monitoring.press_release_metadata`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);

-- ❌ BAD: full table scan
SELECT * FROM `pressure_monitoring.press_release_metadata`
WHERE title LIKE '%earnings%';

-- ✅ BETTER: partition filter + predicate
SELECT * FROM `pressure_monitoring.press_release_metadata`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND title LIKE '%earnings%';

-- ✅ CLUSTER benefit: company filter uses cluster index
SELECT * FROM `pressure_monitoring.press_release_metadata`
WHERE company = 'Apple Inc.'
  AND collection_timestamp >= '2026-01-01';
```

---

## Monitoring

```sql
-- Table sizes
SELECT table_name,
  ROUND(size_bytes / POW(10, 6), 2) AS size_mb,
  row_count
FROM `pressure_monitoring.__TABLES__`
ORDER BY size_bytes DESC;

-- Daily collection stats
SELECT
  DATE(collection_timestamp) AS date,
  COUNT(DISTINCT company) AS companies,
  COUNT(*) AS releases_collected
FROM `pressure_monitoring.press_release_metadata`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY date
ORDER BY date DESC;

-- Scraper performance
SELECT scraper_used, COUNT(*) AS count
FROM `pressure_monitoring.press_release_content`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY scraper_used
ORDER BY count DESC;
```
