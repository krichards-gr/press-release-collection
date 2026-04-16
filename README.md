# Corporate Press Release Collection Pipeline

Automated collection-only pipeline for corporate press releases from Fortune 100 company newsrooms. Uses Bright Data SERP API for search, a 5-tier scraper fallback chain for content extraction, and stores everything in BigQuery. Runs on Google Cloud Run (production) or locally via CLI.

## 🚀 Features

- **Cloud-Native**: Deployed on Google Cloud Run with BigQuery storage
- **Daily Scheduling**: Send a parameterless POST — the pipeline auto-detects where it left off using BigQuery run history (no hardcoded dates needed)
- **Split-Table Schema**: `press_release_metadata` + `press_release_content` — mirrors the earnings-call-collector pattern for clean joins and efficient storage
- **3-Layer Deduplication**: Auto date detection → query-level SERP dedup → URL-level BigQuery dedup; safe and nearly free to re-run
- **HTTP API**: RESTful JSON endpoint for programmatic access
- **Complete Pipeline**: Reference Data → SERP Collection → Article Scraping → BigQuery
- **Multi-Scraper Fallback**: 5-tier scraper chain (newspaper3k → trafilatura → readability → goose3 → Bright Data Unlocker) for 90%+ success rate
- **Full Titles**: Scraped page titles replace truncated SERP titles wherever available
- **Hang Protection**: Per-query timeout skips hung SERP requests; failures logged for retry via alternative API
- **Scalable**: Stateless design, automatic scaling, containerized deployment
- **Production-Ready**: Comprehensive error handling, run logging, and monitoring

## 📋 Requirements

- Python 3.13+
- Google Cloud BigQuery access (for reference data)
- Bright Data SERP API credentials

## 🛠️ Installation

```bash
# Clone repository
git clone <repo-url>
cd press-release-collection

# Install dependencies (uses uv, see .python-version for Python 3.13)
uv sync

# Download required NLP models
python -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy .env template and add your credentials
cp .env.example .env
# Edit .env with your Bright Data credentials
```

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# Bright Data SERP API
BRIGHT_DATA_PROXY_URL=http://brd-customer-xxx-zone-xxx:password@brd.superproxy.io:33335

# Optional: Override defaults
MAX_SERP_PAGES=2
SERP_QUERY_TIMEOUT=20         # Seconds before a hanging query is skipped
SCRAPER_MAX_WORKERS=10
```

### Configuration File (config.py)

All settings centralized in `config.py`:
- File paths
- Timeout values (`SERP_TIMEOUT`, `SERP_QUERY_TIMEOUT`)
- Retry attempts
- Worker counts
- Cache expiration

## 🎯 Usage

### Cloud Run (Production)

**Quick Start**: Run the automated deployment script:

```powershell
# Windows
powershell scripts/deploy.ps1
```

```bash
# Mac/Linux
./scripts/deploy.sh
```

**What it does**:
- ✅ Deploys to Cloud Run from GitHub
- ✅ Sets up Cloud Scheduler for daily automated runs
- ✅ Configures BigQuery and Secret Manager
- ✅ Idempotent — safe to trigger multiple times per day

**Deployment Guides**:
- [DEPLOY_FROM_GITHUB.md](docs/DEPLOY_FROM_GITHUB.md) - Deploy from GitHub repository (recommended)
- [DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) - Quick checklist and testing
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) - Advanced deployment options

**Daily Trigger (no parameters needed)**:
```bash
# The pipeline queries BigQuery for the last run date automatically
curl -X POST $SERVICE_URL
```

**Test with explicit dates**:
```bash
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-10",
    "end_date": "2026-02-11",
    "skip_scraping": true
  }'
```

### CLI (Local/Testing)

```bash
# Run complete pipeline with default dates
python main_cli.py

# Specify custom date range
python main_cli.py --start-date 2026-01-01 --end-date 2026-01-31

# SERP collection only (skip article scraping)
python main_cli.py --skip-scraping
```

### CLI-Only Features

```bash
# Process only new articles since last run
python main_cli.py --incremental

# Process last 7 days
python main_cli.py --last-n-days 7

# Write results to BigQuery (like Cloud Run does)
python main_cli.py --write-to-bigquery

# Force refresh of BigQuery reference data (bypass cache)
python main_cli.py --force-refresh

# Backfill missing publish dates from scraped content
python main_cli.py --backfill-dates
```

## 📊 Pipeline Stages

### 1. Reference Data Collection (`grab_reference_data.py`)
- Always fetches Fortune 100 companies live from BigQuery (single source of truth)
- Saves a local copy to `inputs/reference_data.csv` for downstream use and debugging

### 2. Query Generation (`generate_queries.py`)
- Creates Google search queries for each newsroom URL
- Date-range filtering
- Bright Data format

### 3. SERP Collection (`collect_results.py`)
- Collects search results via Bright Data API
- Pagination support (2 pages default, configurable)
- Per-query 20s hang timeout — active pagination is never interrupted, but stuck queries are skipped and logged
- Failed queries saved to `outputs/serp_failed_queries.csv` for retry via alternative API
- Retry logic with exponential backoff
- Progress tracking

### 4. Article Scraping (`article_scraper.py`)
- Multi-scraper fallback chain:
  1. newspaper3k (fast, NLP features)
  2. trafilatura (robust, bypasses bot protection)
  3. readability (Mozilla algorithm)
  4. goose3 (alternative robust option)
  5. Bright Data Unlocker (paid API, last resort)
- URL filtering (skip pagination/index pages)
- Concurrent processing (10 workers default)
- Scraped page title replaces truncated SERP title wherever available
- Comprehensive error reporting
- **Success rate: 90-95%**

## 📁 Data Storage

### BigQuery Tables (Primary Storage)

```
project.pressure_monitoring/
├── press_release_metadata   # One row per release: SERP fields + company info
├── press_release_content    # Scraped article text (joined by press_release_id)
└── collection_runs          # Pipeline run log: status, dates, queries executed
```

Tables are linked by `press_release_id = MD5(url)`, mirroring the
metadata/content split used in the `earnings-call-collector` project.
All tables are partitioned by timestamp for efficient querying.

> **Legacy**: `collected_articles` still exists in BigQuery with historical data
> from before the schema split. It is no longer written to by the current pipeline.

See [BIGQUERY_SCHEMA.md](docs/BIGQUERY_SCHEMA.md) for full schema details.

### Local Files (Backup/Debug)

```
outputs/
├── f100_collected_results.csv   # SERP results
├── f100_joined.csv               # Joined SERP + scraped content
├── scraper_errors.csv            # Article scraping failures
├── filtered_urls.csv             # Non-article URLs filtered out
└── serp_failed_queries.csv       # SERP queries that timed out or failed
```

## 🔧 Module Reference

### Core Modules

- **`main.py`**: Cloud Run HTTP endpoint (production)
- **`main_cli.py`**: CLI orchestrator (local/testing)
- **`config.py`**: Centralized configuration singleton (`from config import config`)
- **`bigquery_storage.py`**: BigQuery table operations and idempotency logic
- **`grab_reference_data.py`**: Fetches Fortune 100 company list live from BigQuery
- **`generate_queries.py`**: Builds date-scoped Google search queries per newsroom
- **`collect_results.py`**: SERP collection via Bright Data proxy, concurrent with per-query timeout
- **`article_scraper.py`**: 5-tier scraper fallback chain with URL filtering and title enrichment
- **`date_extractor.py`**: 3-layer publish date resolution (URL pattern → article text → fallback)
- **`backfill_company_sector.py`**: Utility to retroactively populate company/sector on existing rows

## 📈 Performance

### SERP Collection
- **Pages per query**: Up to 5 (configurable via `MAX_SERP_PAGES`)
- **Retry attempts**: 3 with exponential backoff
- **Query timeout**: 20 seconds (skips hung queries)
- **Concurrent workers**: 5 (configurable via `SERP_MAX_WORKERS`)

### Article Scraping
- **Success rate**: 90-95%
- **Workers**: 10 concurrent (configurable via `SCRAPER_MAX_WORKERS`)
- **Timeout**: 30 seconds per article

## 🐛 Troubleshooting

### Issue: "No cached data available and BigQuery fetch failed"
**Solution**: Check Google Cloud credentials: `gcloud auth application-default login`

### Issue: "All scrapers failed"
**Solution**: Check the specific domain in `outputs/scraper_errors.csv`. Some sites have strong bot protection.

### Issue: Queries appear in `serp_failed_queries.csv`
**Solution**: These queries timed out (20s) or exhausted retries. They are saved for retry via an alternative API with stronger site-unlocking capabilities.

### Issue: High failure rate on specific domain
**Solution**: Some domains block all scrapers. These are logged in error reports for manual review.

## 📝 Example Workflows

### Daily Automated Collection (Cloud Run — recommended)
```bash
# Cloud Scheduler fires daily — no parameters needed.
# Pipeline auto-detects start_date from last successful run in BigQuery.
curl -X POST $SERVICE_URL
```

### Daily Automated Collection (CLI)
```bash
# Uses BigQuery run history to auto-detect start_date
python main_cli.py --incremental
```

### Last N Days (CLI)
```bash
python main_cli.py --last-n-days 7
```

### Custom Analysis Period
```bash
python main_cli.py --start-date 2026-01-15 --end-date 2026-01-22
```

## 🔐 Security Notes

- ✅ Credentials stored in `.env` (gitignored)
- ✅ SSL verification disabled only for the Bright Data proxy (uses self-signed cert) — all other requests use default verification
- ✅ No hardcoded secrets in code
- ✅ `.env.example` template provided

## 📊 Monitoring & Reporting

Each run produces detailed reports:

```
================================================================================
ARTICLE SCRAPER EXECUTION REPORT
================================================================================

📊 OVERALL STATISTICS:
   Total URLs Found:         202
   🚫 Filtered (non-articles): 6 (3.0%)
   → URLs Attempted:         196
   ✓ Successful:             181 (92.3%)
   ✗ Failed:                 15 (7.7%)

⏱  PERFORMANCE METRICS:
   Total Execution Time:     135.13s (2.3m)
   Average Time per Article: 0.73s
   Throughput:               1.45 articles/sec

🔧 SCRAPER PERFORMANCE:
   newspaper3k...................  136 (75.1%)
   trafilatura...................   41 (22.7%)
   readability...................    4 (  2.2%)
   goose3........................    0 (  0.0%)
```

## 🎓 Development

### Adding a New Scraper

1. Add scraper function to `article_scraper.py`:
```python
def scrape_with_newscraper(url: str) -> Optional[Dict]:
    # Implementation
    return {
        "url": url,
        "scraped_title": ...,   # Full page title (replaces truncated SERP title)
        "article_text": ...,
        "scraper_used": "newscraper"
    }
```

2. Add to fallback chain in `scrape_single_article()`:
```python
scrapers = [
    ("newspaper3k", lambda: scrape_with_newspaper(url, config)),
    ("trafilatura", lambda: scrape_with_trafilatura(url)),
    ("newscraper", lambda: scrape_with_newscraper(url)),  # New!
    ...
]
```

### Customizing Date Ranges

Edit `config.py`:
```python
DEFAULT_START_DATE = '2026-01-01'
DEFAULT_END_DATE = '2026-12-31'
```

## 📜 License

[Your License Here]

## 👥 Contributing

[Contribution Guidelines Here]

## 📞 Support

For issues or questions, please open an issue on GitHub.
