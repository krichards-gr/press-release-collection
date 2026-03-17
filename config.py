"""
Configuration Management for Press Release Collection Pipeline
================================================================

Centralizes ALL configuration settings for the pipeline in one place.
Every setting is loaded from an environment variable with a sensible default,
so you can override anything without touching code.

How it works:
  1. On import, python-dotenv loads variables from a .env file (if present).
  2. The Config class reads those env vars and exposes them as class attributes.
  3. A module-level singleton `config` is created so every other module can do:
         from config import config
     and access any setting directly (e.g. config.MAX_SERP_PAGES).

Directory layout created automatically:
  inputs/           -- cached reference data CSV (from BigQuery)
  outputs/          -- SERP results, joined data, error logs, failed queries
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file (if it exists) so env vars are available below.
# This is a no-op in Cloud Run where env vars are set by the platform.
load_dotenv()


class Config:
    """
    Centralized configuration for the press release collection pipeline.

    Every attribute maps to an environment variable. Defaults are chosen so
    the pipeline works out-of-the-box for local development; only
    BRIGHT_DATA_PROXY_URL (or the HTTP/HTTPS variants) is strictly required.
    """

    # =========================================================================
    # DIRECTORY PATHS
    # =========================================================================
    # All paths are relative to the directory containing this config.py file.
    BASE_DIR = Path(__file__).parent
    INPUTS_DIR = BASE_DIR / "inputs"      # Holds reference_data.csv (cached from BQ)
    OUTPUTS_DIR = BASE_DIR / "outputs"    # Holds all pipeline output CSVs

    # Create directories on import so downstream code can write immediately.
    INPUTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    # =========================================================================
    # BIGQUERY
    # =========================================================================
    # The BigQuery dataset where all pipeline tables live:
    #   - press_release_metadata   (one row per press release)
    #   - press_release_content    (scraped article text)
    #   - collection_runs          (pipeline run log for idempotency)
    BIGQUERY_DATASET = os.getenv('BIGQUERY_DATASET', 'pressure_monitoring')

    # =========================================================================
    # BRIGHT DATA SERP API
    # =========================================================================
    # The pipeline routes Google search requests through Bright Data's SERP proxy.
    # You need at least BRIGHT_DATA_PROXY_URL set in your .env file.
    # If you have separate HTTP and HTTPS proxy URLs, set those instead.

    # Separate HTTP/HTTPS proxy URLs (preferred — some proxies need different
    # URLs for each protocol).
    BRIGHT_DATA_PROXY_URL_HTTP = os.getenv(
        'BRIGHT_DATA_PROXY_URL_HTTP', ''
    ).strip()
    BRIGHT_DATA_PROXY_URL_HTTPS = os.getenv(
        'BRIGHT_DATA_PROXY_URL_HTTPS', ''
    ).strip()

    # Backwards-compatible single URL: if the old env var is set and the
    # protocol-specific ones are not, use it for both HTTP and HTTPS.
    BRIGHT_DATA_PROXY_URL = os.getenv('BRIGHT_DATA_PROXY_URL', '').strip()
    if BRIGHT_DATA_PROXY_URL and not BRIGHT_DATA_PROXY_URL_HTTP:
        BRIGHT_DATA_PROXY_URL_HTTP = BRIGHT_DATA_PROXY_URL
    if BRIGHT_DATA_PROXY_URL and not BRIGHT_DATA_PROXY_URL_HTTPS:
        BRIGHT_DATA_PROXY_URL_HTTPS = BRIGHT_DATA_PROXY_URL

    # -- SERP collection tuning --
    # How many Google result pages to fetch per query (each page ~ 10 results).
    MAX_SERP_PAGES = int(os.getenv('MAX_SERP_PAGES', '2'))

    # Number of retry attempts per individual SERP HTTP request on transient failure.
    SERP_RETRY_ATTEMPTS = int(os.getenv('SERP_RETRY_ATTEMPTS', '3'))

    # Timeout in seconds for a single HTTP request to the SERP proxy.
    SERP_TIMEOUT = int(os.getenv('SERP_TIMEOUT', '15'))

    # Maximum wall-clock seconds allowed for an entire query (across all pages
    # and retries). If a query exceeds this, it's skipped and logged to
    # serp_failed_queries.csv so it can be retried later.
    SERP_QUERY_TIMEOUT = int(os.getenv('SERP_QUERY_TIMEOUT', '20'))

    # Number of concurrent threads executing SERP queries in parallel.
    SERP_MAX_WORKERS = int(os.getenv('SERP_MAX_WORKERS', '5'))

    # Optional: Bright Data Unlocker API key. This is a premium paid scraper
    # used as the LAST fallback in the scraper chain when all free scrapers fail.
    # Leave blank to skip it entirely.
    BRIGHT_DATA_UNLOCKER_API_KEY = os.getenv(
        'BRIGHT_DATA_UNLOCKER_API_KEY', ''
    ).strip()

    # =========================================================================
    # ARTICLE SCRAPER
    # =========================================================================
    # Number of concurrent threads scraping articles in parallel.
    SCRAPER_MAX_WORKERS = int(os.getenv('SCRAPER_MAX_WORKERS', '10'))

    # Per-article HTTP request timeout in seconds.
    SCRAPER_TIMEOUT = int(os.getenv('SCRAPER_TIMEOUT', '30'))

    # Number of retry attempts per scraper (within the fallback chain).
    SCRAPER_RETRY_ATTEMPTS = int(os.getenv('SCRAPER_RETRY_ATTEMPTS', '2'))

    # Delay (seconds) between requests to avoid overwhelming target servers.
    SCRAPER_RATE_LIMIT_DELAY = float(os.getenv('SCRAPER_RATE_LIMIT_DELAY', '0.1'))

    # User-Agent string sent with all scraper HTTP requests.
    SCRAPER_USER_AGENT = os.getenv(
        'SCRAPER_USER_AGENT',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/50.0.2661.102 Safari/537.36'
    )

    # =========================================================================
    # DATA FILES
    # =========================================================================
    # Cached reference data (Fortune 100 companies + newsroom URLs from BQ).
    REFERENCE_DATA_FILE = INPUTS_DIR / "reference_data.csv"

    # Raw SERP results (before scraping). Written by collect_results.py,
    # read by article_scraper.py.
    COLLECTED_RESULTS_FILE = OUTPUTS_DIR / "f100_collected_results.csv"

    # SERP results joined with scraped article content. This is the final
    # local output that gets written to BigQuery.
    JOINED_RESULTS_FILE = OUTPUTS_DIR / "f100_joined.csv"

    # Detailed error log for URLs that failed all scrapers.
    SCRAPER_ERRORS_FILE = OUTPUTS_DIR / "scraper_errors.csv"

    # URLs filtered out by is_valid_article_url() (pagination, home pages, etc.).
    FILTERED_URLS_FILE = OUTPUTS_DIR / "filtered_urls.csv"

    # SERP queries that timed out or failed completely. Saved for manual retry.
    SERP_FAILED_QUERIES_FILE = OUTPUTS_DIR / "serp_failed_queries.csv"

    # =========================================================================
    # PIPELINE SETTINGS
    # =========================================================================
    # Default date range used when no --start-date / --end-date flags are given
    # on the CLI. Cloud Run auto-detects dates from BigQuery instead.
    DEFAULT_START_DATE = os.getenv('DEFAULT_START_DATE', '2026-01-01')
    DEFAULT_END_DATE = os.getenv('DEFAULT_END_DATE', '2026-01-31')

    # =========================================================================
    # LOGGING
    # =========================================================================
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = OUTPUTS_DIR / "pipeline.log"


# ---------------------------------------------------------------------------
# Singleton instance — import this everywhere:
#     from config import config
# ---------------------------------------------------------------------------
config = Config()


if __name__ == "__main__":
    # Quick sanity check: print all settings to verify .env is loaded correctly.
    print("Press Release Collection Pipeline Configuration")
    print("=" * 60)
    print(f"Base Directory:       {Config.BASE_DIR}")
    print(f"Inputs Directory:     {Config.INPUTS_DIR}")
    print(f"Outputs Directory:    {Config.OUTPUTS_DIR}")
    print(f"\nBigQuery Dataset:     {Config.BIGQUERY_DATASET}")
    print(f"\nSERP Settings:")
    print(f"  Max Pages:          {Config.MAX_SERP_PAGES}")
    print(f"  Retry Attempts:     {Config.SERP_RETRY_ATTEMPTS}")
    print(f"  Request Timeout:    {Config.SERP_TIMEOUT}s")
    print(f"  Query Timeout:      {Config.SERP_QUERY_TIMEOUT}s")
    print(f"  Max Workers:        {Config.SERP_MAX_WORKERS}")
    print(f"\nScraper Settings:")
    print(f"  Max Workers:        {Config.SCRAPER_MAX_WORKERS}")
    print(f"  Timeout:            {Config.SCRAPER_TIMEOUT}s")
    print(f"\nData Files:")
    print(f"  Reference Data:     {Config.REFERENCE_DATA_FILE}")
    print(f"  Collected Results:  {Config.COLLECTED_RESULTS_FILE}")
    print(f"  Joined Results:     {Config.JOINED_RESULTS_FILE}")
