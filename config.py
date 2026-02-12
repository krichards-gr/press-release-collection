"""
Configuration Management for Press Release Collection Pipeline
================================================================

This module centralizes all configuration settings for the pipeline.
Settings are loaded from environment variables (.env file) and have sensible defaults.

Usage:
    from config import Config
    config = Config()
    print(config.MAX_WORKERS)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Centralized configuration for the press release collection pipeline."""

    # =============================================================================
    # DIRECTORY PATHS
    # =============================================================================
    BASE_DIR = Path(__file__).parent
    INPUTS_DIR = BASE_DIR / "inputs"
    OUTPUTS_DIR = BASE_DIR / "outputs"

    # Ensure directories exist
    INPUTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    # =============================================================================
    # BIGQUERY
    # =============================================================================
    BIGQUERY_DATASET = os.getenv('BIGQUERY_DATASET', 'pressure_monitoring')

    # =============================================================================
    # BRIGHT DATA SERP API
    # =============================================================================
    BRIGHT_DATA_PROXY_URL_HTTP = os.getenv(
        'BRIGHT_DATA_PROXY_URL_HTTP',
        ''  # No default - require environment variable
    ).strip()

    BRIGHT_DATA_PROXY_URL_HTTPS = os.getenv(
        'BRIGHT_DATA_PROXY_URL_HTTPS',
        ''  # No default - require environment variable
    ).strip()

    # Backwards compatibility - if old variable exists, use it for both
    BRIGHT_DATA_PROXY_URL = os.getenv('BRIGHT_DATA_PROXY_URL', '').strip()
    if BRIGHT_DATA_PROXY_URL and not BRIGHT_DATA_PROXY_URL_HTTP:
        BRIGHT_DATA_PROXY_URL_HTTP = BRIGHT_DATA_PROXY_URL
    if BRIGHT_DATA_PROXY_URL and not BRIGHT_DATA_PROXY_URL_HTTPS:
        BRIGHT_DATA_PROXY_URL_HTTPS = BRIGHT_DATA_PROXY_URL

    # SERP collection settings
    MAX_SERP_PAGES = int(os.getenv('MAX_SERP_PAGES', '10'))  # Increased from 2
    SERP_RETRY_ATTEMPTS = int(os.getenv('SERP_RETRY_ATTEMPTS', '3'))
    SERP_TIMEOUT = int(os.getenv('SERP_TIMEOUT', '30'))

    # =============================================================================
    # ARTICLE SCRAPER
    # =============================================================================
    SCRAPER_MAX_WORKERS = int(os.getenv('SCRAPER_MAX_WORKERS', '10'))
    SCRAPER_TIMEOUT = int(os.getenv('SCRAPER_TIMEOUT', '30'))
    SCRAPER_RETRY_ATTEMPTS = int(os.getenv('SCRAPER_RETRY_ATTEMPTS', '2'))
    SCRAPER_RATE_LIMIT_DELAY = float(os.getenv('SCRAPER_RATE_LIMIT_DELAY', '0.1'))

    SCRAPER_USER_AGENT = os.getenv(
        'SCRAPER_USER_AGENT',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'
    )

    # =============================================================================
    # DATA FILES
    # =============================================================================
    REFERENCE_DATA_FILE = INPUTS_DIR / "reference_data.csv"
    REFERENCE_DATA_CACHE_HOURS = int(os.getenv('REFERENCE_DATA_CACHE_HOURS', '24'))

    COLLECTED_RESULTS_FILE = OUTPUTS_DIR / "f100_collected_results.csv"
    JOINED_RESULTS_FILE = OUTPUTS_DIR / "f100_joined.csv"
    ENRICHED_RESULTS_FILE = OUTPUTS_DIR / "enriched.csv"
    SCRAPER_ERRORS_FILE = OUTPUTS_DIR / "scraper_errors.csv"
    FILTERED_URLS_FILE = OUTPUTS_DIR / "filtered_urls.csv"

    # Checkpointing
    CHECKPOINT_DIR = OUTPUTS_DIR / "checkpoints"
    CHECKPOINT_DIR.mkdir(exist_ok=True)

    # Deduplication
    PROCESSED_URLS_FILE = OUTPUTS_DIR / "processed_urls.txt"

    # =============================================================================
    # PIPELINE SETTINGS
    # =============================================================================
    # Default date range (can be overridden via command line)
    DEFAULT_START_DATE = os.getenv('DEFAULT_START_DATE', '2026-01-01')
    DEFAULT_END_DATE = os.getenv('DEFAULT_END_DATE', '2026-01-31')

    # =============================================================================
    # LOGGING
    # =============================================================================
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = OUTPUTS_DIR / "pipeline.log"


# Create a singleton instance for easy import
config = Config()


if __name__ == "__main__":
    # Print configuration for debugging
    print("Press Release Collection Pipeline Configuration")
    print("=" * 60)
    print(f"Base Directory: {Config.BASE_DIR}")
    print(f"Inputs Directory: {Config.INPUTS_DIR}")
    print(f"Outputs Directory: {Config.OUTPUTS_DIR}")
    print(f"\nSERP Settings:")
    print(f"  Max Pages: {Config.MAX_SERP_PAGES}")
    print(f"  Retry Attempts: {Config.SERP_RETRY_ATTEMPTS}")
    print(f"\nScraper Settings:")
    print(f"  Max Workers: {Config.SCRAPER_MAX_WORKERS}")
    print(f"  Timeout: {Config.SCRAPER_TIMEOUT}s")
    print(f"\nData Files:")
    print(f"  Reference Data: {Config.REFERENCE_DATA_FILE}")
    print(f"  Collected Results: {Config.COLLECTED_RESULTS_FILE}")
    print(f"  Enriched Results: {Config.ENRICHED_RESULTS_FILE}")
