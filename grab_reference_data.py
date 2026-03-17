"""
Reference Data Module
=======================

Fetches Fortune 100 company reference data from BigQuery on every run.
BigQuery is the SINGLE SOURCE OF TRUTH for company newsroom URLs --
always fetching live data ensures the pipeline uses the most current list.

Source table:
  sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference

What we pull:
  - corporation:   Company name (e.g. "Apple Inc.") -- used to tag each press release
  - sector:        Industry sector (e.g. "Technology") -- priority metadata for the user
  - newsroom_url:  The company's pressroom URL (e.g. "newsroom.apple.com") -- this is
                   what we put in the site: operator of the Google SERP query

Filter:
  - F100 IS TRUE       -- only Fortune 100 companies
  - newsroom_url IS NOT NULL  -- skip companies without a known newsroom

A local CSV copy is saved to inputs/reference_data.csv after each fetch.
This file is read by generate_queries.py (which builds SERP queries from it)
and is useful for debugging.
"""

from google.cloud import bigquery
import pandas as pd

from config import config


# ---------------------------------------------------------------------------
# BigQuery query
# ---------------------------------------------------------------------------
# This query pulls the three columns we need from the corporate reference table.
# The ORDER BY Rank keeps Fortune 100 companies sorted by their rank (1-100).

QUERY = """
    SELECT corporation, sector, newsroom_url
    FROM `sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference`
    WHERE F100 IS TRUE
    AND newsroom_url IS NOT NULL
    ORDER BY Rank
"""


def grab_reference_data() -> pd.DataFrame:
    """
    Fetch Fortune 100 company reference data from BigQuery.

    Always fetches live data (no local cache) to ensure newsroom URLs are
    current. If a company changes its pressroom URL or a new company enters
    the Fortune 100, the pipeline picks it up on the next run.

    Returns:
        DataFrame with columns: corporation, sector, newsroom_url
        (one row per Fortune 100 company that has a newsroom URL)

    Raises:
        RuntimeError: If the BigQuery fetch fails (network, auth, etc.)
    """
    print("Fetching reference data from BigQuery...")

    try:
        # Create a BigQuery client using the default credentials
        # (gcloud CLI auth locally, service account in Cloud Run)
        client = bigquery.Client()
        query_job = client.query(QUERY)
        df = query_job.result().to_dataframe()

        print(f"   Fetched {len(df):,} companies from BigQuery")

        # Save a local CSV copy for downstream modules and debugging.
        # generate_queries.py reads this file to build SERP queries.
        df.to_csv(config.REFERENCE_DATA_FILE, index=False)
        print(f"   Saved to: {config.REFERENCE_DATA_FILE}")

        return df

    except Exception as e:
        raise RuntimeError(f"BigQuery fetch failed: {e}") from e


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    df = grab_reference_data()
    print("\nSample data:")
    print(df.head())
