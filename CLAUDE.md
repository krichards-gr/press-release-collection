# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated pipeline for collecting corporate press releases from Fortune 100 company newsrooms. Uses Bright Data SERP API for search, a 5-tier scraper fallback chain for content extraction, and stores everything in BigQuery. Runs on Google Cloud Run (production) or locally via CLI.

**This is a COLLECTION-ONLY pipeline** -- no analysis, no summaries, no keywords. Just collect press releases and their metadata.

**Priority metadata:**
- **company**: Identified by matching `site:url` in the SERP query against `benchmarking_corporate_reference` in BQ
- **sector**: Also from `benchmarking_corporate_reference`
- **publish_date**: Extracted by scrapers from article content/HTML/URL

## Commands

```bash
# Install dependencies (uses uv, see .python-version for Python 3.13)
uv sync

# Download required NLP models
python -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Run pipeline locally (CLI)
python main_cli.py
python main_cli.py --start-date 2026-01-01 --end-date 2026-01-31
python main_cli.py --incremental    # auto-detect dates from last run
python main_cli.py --last-n-days 7
python main_cli.py --skip-scraping  # SERP only, no article scraping
python main_cli.py --write-to-bigquery  # populate BQ like Cloud Run does

# Run tests
python -m pytest tests/

# Deploy to Cloud Run
./scripts/deploy.sh          # Linux/Mac
powershell scripts/deploy.ps1  # Windows
```

## Architecture

**Two entry points:**
- `main.py` — Cloud Run HTTP endpoint (stateless, uses `functions-framework`). Auto-detects date range from BigQuery `collection_runs` table. No CLI args; accepts JSON POST body.
- `main_cli.py` — Local CLI orchestrator. Supports `--incremental`, `--last-n-days`, `--limit`, `--write-to-bigquery`, `--date-update`.

**Pipeline stages (in order):**
1. `grab_reference_data.py` — Fetches Fortune 100 companies (corporation, sector, newsroom_url) from BigQuery
2. `generate_queries.py` — Builds Bright Data SERP queries from newsroom URLs + date range
3. `collect_results.py` — Executes SERP queries via Bright Data proxy with pagination, retry, and per-query timeout (20s hang protection)
4. `article_scraper.py` — Scrapes full article content using fallback chain: newspaper3k → trafilatura → readability → goose3 → Bright Data Unlocker. Concurrent (10 workers).

**Storage (`bigquery_storage.py`):**
- `BigQueryStorage` class manages all table operations
- Split-table schema: `press_release_metadata` + `press_release_content` (joined by `press_release_id = MD5(url)`)
- `collection_runs` — pipeline run log for idempotency
- Metadata includes company, sector, newsroom_url, publish_date

**Configuration (`config.py`):**
- Singleton `config` instance imported everywhere: `from config import config`
- All settings from env vars with defaults; see `.env.example` for required credentials
- Key env vars: `BRIGHT_DATA_PROXY_URL`, `BIGQUERY_DATASET`, `MAX_SERP_PAGES`, `SERP_QUERY_TIMEOUT`, `SCRAPER_MAX_WORKERS`

## Key Design Patterns

- **Collection only**: No analysis, no summaries, no keywords -- just raw press release text + metadata
- **Idempotency**: Safe to re-run; 3-layer dedup (auto date detection → query-level SERP dedup → URL-level BigQuery dedup via MD5 IDs)
- **press_release_id**: Deterministic `MD5(url)` — same URL always gets the same ID across runs
- **Scraper fallback chain**: 5 scrapers tried in order (fast/free first, paid last)
- **Failed queries**: Timed-out SERP queries saved to `outputs/serp_failed_queries.csv` for retry
- **Local outputs are gitignored**: All CSVs in `inputs/` and `outputs/` are ephemeral debug artifacts; BigQuery is the source of truth
