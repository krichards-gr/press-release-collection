# Corporate Press Release Collection Pipeline

Automated end-to-end pipeline for collecting and analyzing corporate press releases from Fortune 100 company newsrooms. Designed for Google Cloud Run with BigQuery storage.

## 🚀 Features

- **Cloud-Native**: Deployed on Google Cloud Run with BigQuery storage
- **HTTP API**: RESTful JSON endpoint for programmatic access
- **Complete Pipeline**: Reference Data → SERP Collection → Article Scraping → Sentiment Analysis → BigQuery
- **Multi-Scraper Fallback**: 4-tier scraper chain (newspaper3k → trafilatura → readability → goose3) for 90%+ success rate
- **Scalable**: Stateless design, automatic scaling, containerized deployment
- **Schedulable**: Cloud Scheduler integration for automated runs
- **Production-Ready**: Comprehensive error handling, monitoring, and logging

## 📋 Requirements

- Python 3.13+
- Google Cloud BigQuery access (for reference data)
- Bright Data SERP API credentials
- spaCy English model: `python -m spacy download en_core_web_lg`

## 🛠️ Installation

```bash
# Clone repository
git clone <repo-url>
cd press-release-collection

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Download spaCy model
python -m spacy download en_core_web_lg

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
MAX_SERP_PAGES=10
SCRAPER_MAX_WORKERS=10
REFERENCE_DATA_CACHE_HOURS=24
```

### Configuration File (config.py)

All settings centralized in `config.py`:
- File paths
- Timeout values
- Retry attempts
- Worker counts
- Cache expiration

## 🎯 Usage

### Cloud Run (Production)

**Quick Start**: Run the automated deployment script:

```powershell
# Windows
.\deploy.ps1
```

```bash
# Mac/Linux
chmod +x deploy.sh
./deploy.sh
```

**What it does**:
- ✅ Deploys to Cloud Run from GitHub
- ✅ Sets up Cloud Scheduler (3 daily runs: midnight, noon, 4pm EST)
- ✅ Configures BigQuery and Secret Manager
- ✅ Includes idempotency and automatic backfill

**Deployment Guides**:
- [DEPLOY_FROM_GITHUB.md](DEPLOY_FROM_GITHUB.md) - Deploy from GitHub repository (recommended)
- [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) - Quick checklist and testing
- [DEPLOYMENT.md](DEPLOYMENT.md) - Advanced deployment options

**Test Deployment**:
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

# Resume from latest checkpoint
python main_cli.py --resume

# Force refresh of BigQuery reference data (bypass cache)
python main_cli.py --force-refresh
```

## 📊 Pipeline Stages

### 1. Reference Data Collection (`grab_reference_data.py`)
- Fetches Fortune 100 companies from BigQuery
- Intelligent caching (24-hour default)
- Fallback to expired cache if BigQuery unavailable

### 2. Query Generation (`generate_queries.py`)
- Creates Google search queries for each newsroom
- Date-range filtering
- Bright Data format

### 3. SERP Collection (`collect_results.py`)
- Collects search results via Bright Data API
- Pagination support (10 pages default)
- Retry logic with exponential backoff
- Progress tracking

### 4. Article Scraping (`article_scraper.py`)
- Multi-scraper fallback chain:
  1. newspaper3k (fast, NLP features)
  2. trafilatura (robust, bypasses bot protection)
  3. readability (Mozilla algorithm)
  4. goose3 (alternative robust option)
- URL filtering (skip pagination/index pages)
- Concurrent processing (10 workers default)
- Comprehensive error reporting
- **Success rate: 90-95%**

### 5. Sentiment Analysis
- spaCy + asent for sentiment classification
- Categories: positive, negative, neutral
- Applied to article descriptions

## 📁 Data Storage

### BigQuery Tables (Primary Storage)

```
project.press_release_collection/
├── serp_results          # Raw search results
├── scraped_articles      # Full article content
└── enriched_articles     # Final data with sentiment
```

All tables are partitioned by `collection_timestamp` for efficient querying.

### Local Files (Backup/Debug)

```
outputs/
├── f100_collected_results.csv   # SERP results backup
├── f100_joined.csv               # Joined data backup
├── enriched.csv                  # Enriched data backup
├── scraper_errors.csv            # Failed URLs log
└── filtered_urls.csv             # Non-article URLs filtered
```

## 🔧 Module Reference

### Core Modules

- **`main.py`**: Cloud Run HTTP endpoint (production)
- **`main_cli.py`**: CLI orchestrator (local/testing)
- **`config.py`**: Centralized configuration
- **`bigquery_storage.py`**: BigQuery table operations
- **`grab_reference_data.py`**: Reference data fetching with caching
- **`generate_queries.py`**: Search query generation
- **`collect_results.py`**: SERP collection with retry logic
- **`article_scraper.py`**: Multi-scraper content extraction

### Utility Modules

- **`deduplication.py`**: URL tracking (CLI only)
- **`checkpointing.py`**: Fault tolerance (CLI only)

## 📈 Performance

### SERP Collection
- **Throughput**: ~5-10 queries/second
- **Pages per query**: Up to 10 (configurable)
- **Retry attempts**: 3 with exponential backoff

### Article Scraping
- **Throughput**: ~1-2 articles/second
- **Success rate**: 90-95%
- **Workers**: 10 concurrent (configurable)
- **Timeout**: 30 seconds per article

### Improvements from Original
- **Success rate**: 67% → 90%+ (+23 points)
- **Coverage**: 2 pages → 10 pages (+400%)
- **Resilience**: No checkpoints → Full fault tolerance
- **Efficiency**: No deduplication → Smart URL tracking

## 🐛 Troubleshooting

### Issue: "No cached data available and BigQuery fetch failed"
**Solution**: Check Google Cloud credentials: `gcloud auth application-default login`

### Issue: "All scrapers failed"
**Solution**: Check specific domain in `scraper_errors.csv`. Some sites have strong bot protection.

### Issue: "SSL verification failed"
**Solution**: Ensure `verify=True` in `collect_results.py` (already default). Check network/proxy.

### Issue: High failure rate on specific domain
**Solution**: Some domains block all scrapers. These are logged in error reports for manual review.

## 📝 Example Workflows

### Daily Automated Collection
```bash
# Cron job: Run daily at 2 AM
0 2 * * * cd /path/to/pipeline && python main.py --last-n-days 1
```

### Weekly Full Refresh
```bash
# Weekly: Collect last 7 days
python main.py --last-n-days 7 --force-refresh
```

### Custom Analysis Period
```bash
# Specific event period
python main.py --start-date 2026-01-15 --end-date 2026-01-22
```

## 🔐 Security Notes

- ✅ Credentials stored in `.env` (gitignored)
- ✅ SSL verification enabled
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
        "title": ...,
        "url": url,
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
