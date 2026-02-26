"""
Reference Data Management Module
==================================

Fetches Fortune 100 company reference data directly from BigQuery on every run.
BigQuery is the single source of truth for pressroom URLs — always fetching live
ensures the pipeline uses the most current data.

A local copy is saved to inputs/reference_data.csv after each fetch for use
by downstream modules (generate_queries.py) and for debugging.
"""

from google.cloud import bigquery
import pandas as pd

from config import config


# BigQuery query for F100 companies with newsroom URLs
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

    Always fetches live data to ensure pressroom URLs are current.
    Saves a local copy to inputs/reference_data.csv for downstream use.

    Returns:
        DataFrame with columns: corporation, sector, newsroom_url

    Raises:
        RuntimeError: If the BigQuery fetch fails
    """
    print("☁️  Fetching reference data from BigQuery...")

    try:
        client = bigquery.Client()
        query_job = client.query(QUERY)
        df = query_job.result().to_dataframe()

        print(f"   ✓ Fetched {len(df):,} companies from BigQuery")

        # Save local copy for generate_queries.py and debugging
        df.to_csv(config.REFERENCE_DATA_FILE, index=False)
        print(f"   💾 Saved to: {config.REFERENCE_DATA_FILE}")

        return df

    except Exception as e:
        raise RuntimeError(f"BigQuery fetch failed: {e}") from e


if __name__ == "__main__":
    df = grab_reference_data()
    print("\nSample data:")
    print(df.head())
