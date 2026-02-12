"""
SERP Results Collection Module
================================

Collects search engine results from Google via Bright Data SERP API.
Handles pagination, retries, and error recovery.

Features:
- Configurable pagination depth (default: 10 pages)
- Automatic retry logic for transient failures
- Progress tracking with tqdm
- Comprehensive error logging
"""

import json
import pandas as pd
from brightdata import BrightDataClient
import requests
import time
from urllib.parse import urlparse
from typing import List, Optional
from tqdm import tqdm

from config import config


def collect_search_results(search_queries: List[str], max_pages: int = None) -> Optional[pd.DataFrame]:
    """
    Collect search results from multiple queries with pagination and retry logic.

    Parameters:
    -----------
    search_queries : List[str]
        List of constructed search query URLs to process
    max_pages : int, optional
        Maximum pages to fetch per query (defaults to config.MAX_SERP_PAGES)

    Returns:
    --------
    Optional[pd.DataFrame]
        Combined dataframe with all results, or None if no results are found.
    """
    if max_pages is None:
        max_pages = config.MAX_SERP_PAGES

    # Ensure Bright Data proxy is configured to avoid direct Google requests
    if not config.BRIGHT_DATA_PROXY_URL_HTTP or not config.BRIGHT_DATA_PROXY_URL_HTTPS:
        raise ValueError(
            "Bright Data proxy URLs are not configured. "
            "Set BRIGHT_DATA_PROXY_URL or BRIGHT_DATA_PROXY_URL_HTTP/HTTPS in the environment."
        )
    else:
        http_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTP).netloc
        https_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTPS).netloc
        print(f"Using Bright Data proxy hosts: http={http_host}, https={https_host}")

        # Debug: Show full proxy URLs (mask password for security)
        def mask_password(url):
            """Mask password in proxy URL for safe logging"""
            if '@' in url:
                creds, rest = url.split('@', 1)
                if ':' in creds:
                    protocol_user, password = creds.rsplit(':', 1)
                    return f"{protocol_user}:***@{rest}"
            return url

        print(f"DEBUG - HTTP proxy: {mask_password(config.BRIGHT_DATA_PROXY_URL_HTTP)}")
        print(f"DEBUG - HTTPS proxy: {mask_password(config.BRIGHT_DATA_PROXY_URL_HTTPS)}")
        print(f"DEBUG - Proxy format check: HTTP starts with 'http://'? {config.BRIGHT_DATA_PROXY_URL_HTTP.startswith('http://')}")
        print(f"DEBUG - Proxy format check: HTTPS starts with 'http://'? {config.BRIGHT_DATA_PROXY_URL_HTTPS.startswith('http://')}")

    # Accumulator for all search results across queries and pages
    full_results = []
    failed_queries = []

    # Progress bar for queries
    pbar = tqdm(
        search_queries,
        desc="Collecting SERP Results",
        unit="query",
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
    )

    # Process each search query
    for query in pbar:
        current_url = query
        page_count = 0
        query_results = []

        # Paginate through results
        while current_url and page_count < max_pages:
            success = False

            # Retry logic for transient failures
            for attempt in range(config.SERP_RETRY_ATTEMPTS):
                try:
                    # Send request through Bright Data SERP proxy
                    response = requests.get(
                        current_url,
                        proxies={
                            'http': config.BRIGHT_DATA_PROXY_URL_HTTP,
                            'https': config.BRIGHT_DATA_PROXY_URL_HTTPS
                        },
                        timeout=config.SERP_TIMEOUT,
                        verify=True  # SSL verification enabled for security
                    )
                    response.raise_for_status()

                    # Parse JSON response
                    try:
                        parsed = json.loads(response.text)
                    except json.JSONDecodeError as e:
                        content_type = response.headers.get("content-type", "unknown")
                        body_snippet = (response.text or "")[:200].replace("\n", " ")
                        resp_url = response.url if response is not None else "unknown"
                        tqdm.write(
                            f"⚠️ JSON decode error for query: {e} "
                            f"(status={response.status_code}, content-type={content_type}, "
                            f"url={resp_url}, request_url={current_url}, body='{body_snippet}')"
                        )
                        if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                            time.sleep(2 ** attempt)
                            continue
                        parsed = {"organic": []}  # Empty result on parse error after retries

                    # Check for organic results
                    if not parsed.get("organic"):
                        break  # No more results

                    # Extract and standardize fields
                    df = pd.DataFrame(parsed["organic"])
                    required_columns = ["title", "description", "link", "rank"]
                    for col in required_columns:
                        if col not in df.columns:
                            df[col] = None

                    data = df[required_columns]
                    data["query"] = parsed["general"]["query"]
                    query_results.append(data)

                    # Get next page
                    pagination = parsed.get("pagination", {})
                    next_page_link = pagination.get("next_page_link") if pagination else None
                    current_url = next_page_link + "&brd_json=1" if next_page_link else None

                    page_count += 1
                    success = True
                    break  # Success, exit retry loop

                except requests.exceptions.Timeout:
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        tqdm.write(f"⚠️  Timeout after {config.SERP_RETRY_ATTEMPTS} attempts: {current_url[:100]}...")
                        break

                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    if status_code == 429 and attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(10 * (attempt + 1))
                        continue
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        tqdm.write(f"⚠️  Request failed: {str(e)[:100]}")
                        break

                except requests.exceptions.RequestException as e:
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        tqdm.write(f"⚠️  Request failed: {str(e)[:100]}")
                        break

                except Exception as e:
                    tqdm.write(f"⚠️  Unexpected error: {str(e)[:100]}")
                    break

            if not success and page_count == 0:
                # Failed to get even the first page
                failed_queries.append(query)

        # Add this query's results to the full collection
        if query_results:
            full_results.extend(query_results)

        # Rate limiting: small delay between queries to avoid 429 errors
        time.sleep(0.5)  # 500ms delay between queries

        # Update progress bar
        pbar.set_postfix(
            pages=page_count,
            results=sum(len(df) for df in full_results)
        )

    pbar.close()

    # Report on failed queries
    if failed_queries:
        print(f"\n⚠️  {len(failed_queries)} queries failed completely:")
        for fq in failed_queries[:5]:  # Show first 5
            print(f"   - {fq[:100]}...")
        if len(failed_queries) > 5:
            print(f"   ... and {len(failed_queries) - 5} more")

    # Combine all results into final dataframe
    if full_results:
        final_df = pd.concat(full_results, ignore_index=True)
        print(f"\n✅ Collected {len(final_df):,} SERP results from {len(search_queries)} queries")
        return final_df
    else:
        print("\n⚠️  No results returned from any query")
        return None
