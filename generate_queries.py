"""
Search Query Generation Module
================================

Generates Google SERP queries for each Fortune 100 company newsroom URL,
scoped to a specified date range and formatted for the Bright Data SERP API.

Query format:
    https://www.google.com/search?q=site:{newsroom_url}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1
"""

import pandas as pd


def create_search_queries(start_date, end_date, limit=None):
    """
    Generate one search query per newsroom URL from reference data.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        limit: If set, only use the first N companies (useful for local testing)

    Returns:
        List of formatted Google search query URLs
    """
    queries = []

    df = pd.read_csv('inputs/reference_data.csv')
    if limit is not None:
        df = df.head(limit)
        print(f"⚠️  Limit applied: using first {len(df)} companies for query generation")
    pressroom_urls = df['newsroom_url'].dropna().tolist()

    print(f"📝 Loaded {len(pressroom_urls)} valid newsroom URLs from reference data")

    for url in pressroom_urls:
        if not url or not isinstance(url, str) or url.strip() == '':
            print(f"⚠️ Skipping invalid URL: {url}")
            continue

        url = url.strip()
        query = f'https://www.google.com/search?q=site:{url}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1'
        queries.append(query)

    print(f"✅ Generated {len(queries)} search queries")

    return queries
