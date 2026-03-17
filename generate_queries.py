"""
Search Query Generation Module
================================

Generates Google SERP queries for each Fortune 100 company newsroom URL.
Each query is scoped to a date range and formatted for the Bright Data SERP API.

Query format:
  https://www.google.com/search?q=site:{newsroom_url}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1

How it works:
  1. Reads inputs/reference_data.csv (produced by grab_reference_data.py)
  2. For each company's newsroom_url, builds a Google site: search query
     restricted to articles published within the date range
  3. Returns a list of query URLs ready to send to Bright Data's SERP proxy

URL parameters explained:
  - q=site:{url}  -- restrict results to this domain
  - before:/after: -- Google's date range operators
  - gl=US         -- country (United States)
  - hl=en         -- language (English)
  - brd_json=1    -- tells Bright Data proxy to return JSON (not HTML)

Input:
  inputs/reference_data.csv  (must exist -- run grab_reference_data.py first)

Output:
  List of query URL strings (one per company)
"""

import pandas as pd


def create_search_queries(start_date: str, end_date: str,
                          limit: int = None) -> list:
    """
    Generate one Google SERP query per company newsroom URL.

    Args:
        start_date: Start date in YYYY-MM-DD format (inclusive).
        end_date:   End date in YYYY-MM-DD format (inclusive).
        limit:      If set, only use the first N companies from the reference
                    data. Useful for local testing without burning through
                    the full SERP API quota.

    Returns:
        List of fully-formed Google search query URLs ready for the
        Bright Data SERP proxy.
    """
    queries = []

    # Read the reference data CSV produced by grab_reference_data.py.
    # Each row has: corporation, sector, newsroom_url
    df = pd.read_csv('inputs/reference_data.csv')

    # Apply the limit if set (for testing with fewer companies)
    if limit is not None:
        df = df.head(limit)
        print(f"Limit applied: using first {len(df)} companies for query generation")

    # Extract the list of newsroom URLs, dropping any null/empty values
    pressroom_urls = df['newsroom_url'].dropna().tolist()
    print(f"Loaded {len(pressroom_urls)} valid newsroom URLs from reference data")

    # Build one query per newsroom URL
    for url in pressroom_urls:
        # Skip invalid entries
        if not url or not isinstance(url, str) or url.strip() == '':
            print(f"Skipping invalid URL: {url}")
            continue

        url = url.strip()

        # Build the Google SERP query with date range operators.
        # The +before:/{+after:} operators restrict results to articles
        # published within the specified date range.
        query = (
            f'https://www.google.com/search'
            f'?q=site:{url}+before:{end_date}+after:{start_date}'
            f'&gl=US&hl=en&brd_json=1'
        )
        queries.append(query)

    print(f"Generated {len(queries)} search queries")
    return queries
