"""
Reference Data Management Module
==================================

Fetches Fortune 100 company reference data from BigQuery with intelligent caching.
Avoids unnecessary API calls by caching results locally.

Features:
- Automatic caching to avoid repeated BigQuery hits
- Configurable cache expiration (default: 24 hours)
- Fallback to cached data if BigQuery is unavailable
"""

from google.cloud import bigquery
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

from config import config


# BigQuery query for F100 companies with newsroom URLs
QUERY = """
    SELECT corporation, sector, newsroom_url
    FROM `sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference`
    WHERE F100 IS TRUE
    AND newsroom_url IS NOT NULL
"""


def is_cache_valid(cache_file: Path, max_age_hours: int = None) -> bool:
    """Check if cached reference data is still fresh."""
    if max_age_hours is None:
        max_age_hours = config.REFERENCE_DATA_CACHE_HOURS

    if not cache_file.exists():
        return False

    # Check file age
    file_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
    age = datetime.now() - file_time

    return age < timedelta(hours=max_age_hours)


def grab_reference_data(force_refresh: bool = False) -> pd.DataFrame:
    """
    Fetch Fortune 100 company reference data with intelligent caching.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data from BigQuery

    Returns:
        DataFrame with columns: corporation, sector, newsroom_url
    """
    cache_file = config.REFERENCE_DATA_FILE

    # Try to use cached data first
    if not force_refresh and is_cache_valid(cache_file):
        print(f"📂 Loading reference data from cache: {cache_file}")
        try:
            df = pd.read_csv(cache_file)
            print(f"   ✓ Loaded {len(df):,} companies from cache")
            return df
        except Exception as e:
            print(f"   ⚠️  Cache read failed: {e}")
            # Fall through to fetch fresh data

    # Fetch fresh data from BigQuery
    print("☁️  Fetching reference data from BigQuery...")
    try:
        client = bigquery.Client()
        query_job = client.query(QUERY)
        rows = query_job.result()
        df = rows.to_dataframe()

        print(f"   ✓ Fetched {len(df):,} companies from BigQuery")

        # Save to cache
        df.to_csv(cache_file, index=False)
        print(f"   💾 Cached to: {cache_file}")

        return df

    except Exception as e:
        print(f"   ❌ BigQuery fetch failed: {e}")

        # Fallback to cached data even if expired
        if cache_file.exists():
            print(f"   ⚠️  Falling back to expired cache")
            df = pd.read_csv(cache_file)
            print(f"   ✓ Loaded {len(df):,} companies from expired cache")
            return df
        else:
            raise RuntimeError("No cached data available and BigQuery fetch failed")


if __name__ == "__main__":
    # Test the function
    df = grab_reference_data()
    print("\nSample data:")
    print(df.head())
