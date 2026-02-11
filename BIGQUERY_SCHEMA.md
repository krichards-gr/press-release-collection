# BigQuery Schema Documentation

## Dataset: `pressure_monitoring`

This dataset contains press release collection data with a 2-table design that separates raw content from enrichments.

---

## Table 1: `collected_articles`

**Purpose**: Stores all raw article data (SERP + scraped content). Immutable once collected.

**Schema**:

| Column | Type | Mode | Description |
|--------|------|------|-------------|
| `url` | STRING | REQUIRED | Article URL (primary key) |
| `title` | STRING | NULLABLE | Article title from SERP |
| `description` | STRING | NULLABLE | Meta description from SERP |
| `rank` | INTEGER | NULLABLE | Search result rank position |
| `query` | STRING | NULLABLE | Search query that found this article |
| `article_text` | STRING | NULLABLE | Full article text |
| `summary` | STRING | NULLABLE | Auto-generated summary |
| `keywords` | STRING | NULLABLE | Extracted keywords (comma-separated) |
| `publish_date` | TIMESTAMP | NULLABLE | Article publication date |
| `scraper_used` | STRING | NULLABLE | Which scraper extracted content |
| `collection_timestamp` | TIMESTAMP | REQUIRED | When article was collected |
| `run_id` | STRING | NULLABLE | Pipeline run identifier |

**Features**:
- **Partitioned** by `collection_timestamp` (day)
- **Clustered** by `url`
- **Append-only**: Articles are never updated once collected

**Example Query**:
```sql
-- Get recently collected articles
SELECT
  url,
  title,
  publish_date,
  scraper_used,
  collection_timestamp
FROM `pressure_monitoring.collected_articles`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY collection_timestamp DESC
LIMIT 100;
```

---

## Table 2: `article_enrichments`

**Purpose**: Stores analysis results (sentiment, entities, issues). Can be regenerated/versioned.

**Schema**:

| Column | Type | Mode | Description |
|--------|------|------|-------------|
| `url` | STRING | REQUIRED | Article URL (foreign key to collected_articles) |
| `sentiment` | STRING | NULLABLE | Sentiment label: positive, negative, neutral |
| `sentiment_score` | FLOAT | NULLABLE | Sentiment confidence score (0-1) |
| `issue_labels` | STRING | REPEATED | Identified issues/topics |
| `entity_labels` | STRING | REPEATED | Named entities mentioned |
| `custom_metadata` | JSON | NULLABLE | Additional metadata as JSON |
| `enrichment_timestamp` | TIMESTAMP | REQUIRED | When enrichments were generated |
| `enrichment_version` | STRING | NULLABLE | Version of enrichment pipeline |
| `run_id` | STRING | NULLABLE | Pipeline run identifier |

**Features**:
- **Partitioned** by `enrichment_timestamp` (day)
- **Clustered** by `url`
- **Append-only**: Allows tracking enrichment changes over time
- **Versioned**: Multiple enrichment versions can coexist

**Example Query**:
```sql
-- Get latest enrichments for each article
WITH latest_enrichments AS (
  SELECT
    url,
    sentiment,
    sentiment_score,
    issue_labels,
    entity_labels,
    enrichment_timestamp,
    enrichment_version,
    ROW_NUMBER() OVER (
      PARTITION BY url
      ORDER BY enrichment_timestamp DESC
    ) as rn
  FROM `pressure_monitoring.article_enrichments`
)
SELECT *
FROM latest_enrichments
WHERE rn = 1;
```

---

## Joining Tables

**Get articles with their latest enrichments**:

```sql
SELECT
  c.url,
  c.title,
  c.article_text,
  c.publish_date,
  c.collection_timestamp,
  e.sentiment,
  e.sentiment_score,
  e.issue_labels,
  e.entity_labels
FROM `pressure_monitoring.collected_articles` c
LEFT JOIN (
  SELECT
    url,
    sentiment,
    sentiment_score,
    issue_labels,
    entity_labels,
    ROW_NUMBER() OVER (PARTITION BY url ORDER BY enrichment_timestamp DESC) as rn
  FROM `pressure_monitoring.article_enrichments`
) e
  ON c.url = e.url AND e.rn = 1
WHERE c.collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
ORDER BY c.collection_timestamp DESC;
```

---

## Future Enrichment Fields

The `article_enrichments` table is designed to accommodate future enrichments:

### Issue Labels
```sql
-- Articles about specific issues
SELECT
  url,
  issue_labels,
  sentiment
FROM `pressure_monitoring.article_enrichments`
WHERE 'climate' IN UNNEST(issue_labels);
```

### Entity Labels
```sql
-- Articles mentioning specific entities
SELECT
  url,
  entity_labels,
  sentiment
FROM `pressure_monitoring.article_enrichments`
WHERE 'CEO_name' IN UNNEST(entity_labels);
```

### Custom Metadata
```sql
-- Articles with custom metadata
SELECT
  url,
  JSON_VALUE(custom_metadata, '$.custom_field') as custom_field
FROM `pressure_monitoring.article_enrichments`
WHERE custom_metadata IS NOT NULL;
```

---

## Schema Management

### Creating Tables

Tables are auto-created by the pipeline on first run:

```python
from bigquery_storage import BigQueryStorage

storage = BigQueryStorage()
storage.initialize_tables()
```

### Versioning Enrichments

When you update your enrichment pipeline:

```python
# Write new enrichments with updated version
storage.write_article_enrichments(
    enrichments_df,
    run_id=run_id,
    enrichment_version="v2.0"  # New version
)
```

### Finding Articles Needing Re-enrichment

```python
# Get URLs without v2.0 enrichments
urls = storage.get_urls_needing_enrichment(enrichment_version="v2.0")
```

---

## Best Practices

1. **Never delete from `collected_articles`** - It's your source of truth
2. **Version your enrichments** - Use semantic versioning (v1.0, v1.1, v2.0)
3. **Partition pruning** - Always filter by timestamp columns for efficiency
4. **Use clustering** - Queries filtering by URL are optimized
5. **Batch writes** - Write enrichments in batches, not row-by-row
6. **Monitor costs** - Use BigQuery's query cost estimator

---

## Cost Optimization

```sql
-- ✅ GOOD: Uses partition filter
SELECT * FROM `pressure_monitoring.collected_articles`
WHERE collection_timestamp >= '2026-01-01';

-- ❌ BAD: Full table scan
SELECT * FROM `pressure_monitoring.collected_articles`
WHERE title LIKE '%keyword%';

-- ✅ BETTER: Partition filter + title filter
SELECT * FROM `pressure_monitoring.collected_articles`
WHERE collection_timestamp >= '2026-01-01'
  AND title LIKE '%keyword%';
```

---

## Monitoring

### Storage Size
```sql
SELECT
  table_name,
  ROUND(size_bytes / POW(10, 9), 2) as size_gb,
  row_count
FROM `pressure_monitoring.__TABLES__`
ORDER BY size_bytes DESC;
```

### Collection Stats
```sql
SELECT
  DATE(collection_timestamp) as date,
  COUNT(*) as articles_collected,
  COUNT(DISTINCT scraper_used) as scrapers_used,
  COUNTIF(article_text IS NOT NULL) as successful_scrapes
FROM `pressure_monitoring.collected_articles`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY date
ORDER BY date DESC;
```

### Enrichment Coverage
```sql
SELECT
  COUNT(DISTINCT c.url) as total_articles,
  COUNT(DISTINCT e.url) as enriched_articles,
  ROUND(COUNT(DISTINCT e.url) / COUNT(DISTINCT c.url) * 100, 2) as coverage_pct
FROM `pressure_monitoring.collected_articles` c
LEFT JOIN `pressure_monitoring.article_enrichments` e ON c.url = e.url
WHERE c.collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
```
