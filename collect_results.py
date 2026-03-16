"""
SERP Results Collection Module
================================

Collects search engine results from Google via Bright Data SERP API.
Handles pagination, retries, error recovery, and per-query timeouts.

Features:
- Configurable pagination depth (default: 2 pages)
- Per-query wall-clock timeout (SERP_QUERY_TIMEOUT, default: 20s) — skips
  queries that hang without interrupting active pagination
- Automatic retry logic for transient failures
- Progress tracking with tqdm
- Failed queries logged to outputs/serp_failed_queries.csv with failure reason
  (reason: "timed_out" or "request_failed") for later retry via alternative API
"""

import json
import pandas as pd
import requests
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
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

        # Test proxy connectivity (with retries)
        print(f"DEBUG - Testing proxy connectivity...")
        proxy_test_attempts = 3
        proxy_ok = False
        for attempt in range(proxy_test_attempts):
            try:
                test_response = requests.get(
                    "https://www.google.com/search?q=test&brd_json=1",
                    proxies={
                        'http': config.BRIGHT_DATA_PROXY_URL_HTTP,
                        'https': config.BRIGHT_DATA_PROXY_URL_HTTPS
                    },
                    timeout=20,
                    verify=False
                )
                print(f"DEBUG - Proxy test successful! Status code: {test_response.status_code}")
                print(f"DEBUG - Response length: {len(test_response.text)} bytes")
                proxy_ok = True
                break
            except Exception as test_error:
                wait = 2 ** attempt
                print(f"⚠️  Proxy test attempt {attempt + 1}/{proxy_test_attempts} failed: "
                      f"{type(test_error).__name__}: {str(test_error)[:200]}")
                if attempt < proxy_test_attempts - 1:
                    print(f"    Retrying in {wait}s...")
                    time.sleep(wait)

        if not proxy_ok:
            raise RuntimeError(
                "Proxy connectivity test failed after all retries. "
                "Check your Bright Data credentials and network connection before retrying."
            )

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
        query_timed_out = False
        query_start_time = time.time()

        tqdm.write(f"\n→ Collecting: {query}")

        # Paginate through results
        while current_url and page_count < max_pages:
            # Check total elapsed time for this query before attempting another page
            elapsed = time.time() - query_start_time
            if elapsed > config.SERP_QUERY_TIMEOUT:
                tqdm.write(f"⏱  Query timed out ({elapsed:.0f}s > {config.SERP_QUERY_TIMEOUT}s), skipping: {query[:100]}...")
                query_timed_out = True
                break

            success = False

            # Retry logic for transient failures
            for attempt in range(config.SERP_RETRY_ATTEMPTS):
                # Also check before each retry attempt
                if time.time() - query_start_time > config.SERP_QUERY_TIMEOUT:
                    elapsed = time.time() - query_start_time
                    tqdm.write(f"⏱  Query timed out ({elapsed:.0f}s > {config.SERP_QUERY_TIMEOUT}s), skipping: {query[:100]}...")
                    query_timed_out = True
                    break
                try:
                    # Use the lesser of the per-request timeout and remaining query budget
                    # so a single request can never exceed the query deadline
                    remaining = max(1, config.SERP_QUERY_TIMEOUT - (time.time() - query_start_time))
                    request_timeout = min(config.SERP_TIMEOUT, remaining)

                    # Send request through Bright Data SERP proxy
                    response = requests.get(
                        current_url,
                        proxies={
                            'http': config.BRIGHT_DATA_PROXY_URL_HTTP,
                            'https': config.BRIGHT_DATA_PROXY_URL_HTTPS
                        },
                        timeout=request_timeout,
                        verify=False  # Disable SSL verification for Bright Data proxy (uses self-signed cert)
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
                        tqdm.write(f"⚠️  HTTP Error: {str(e)[:300]}")
                        if e.response is not None:
                            tqdm.write(f"    Response body: {e.response.text[:500]}")
                        break

                except requests.exceptions.RequestException as e:
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        error_msg = str(e)
                        tqdm.write(f"⚠️  Request failed: {error_msg[:300]}")
                        tqdm.write(f"    Error type: {type(e).__name__}")
                        tqdm.write(f"    Query URL: {current_url[:200]}")
                        break

                except Exception as e:
                    tqdm.write(f"⚠️  Unexpected error: {str(e)[:100]}")
                    break

            if query_timed_out:
                break  # Exit the pagination while loop

            if not success and page_count == 0:
                # Failed to get even the first page
                failed_queries.append({"query": query, "reason": "request_failed"})

        if query_timed_out and page_count == 0:
            failed_queries.append({"query": query, "reason": "timed_out"})

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

    # Report and save failed queries
    if failed_queries:
        print(f"\n⚠️  {len(failed_queries)} queries failed completely:")
        for fq in failed_queries[:5]:  # Show first 5
            print(f"   - [{fq['reason']}] {fq['query'][:100]}...")
        if len(failed_queries) > 5:
            print(f"   ... and {len(failed_queries) - 5} more")

        # Append to the failed queries log for later retry via alternative API
        failed_df = pd.DataFrame(failed_queries)
        failed_path = config.SERP_FAILED_QUERIES_FILE
        write_header = not failed_path.exists()
        failed_df.to_csv(failed_path, mode='a', index=False, header=write_header)
        print(f"   Saved to {failed_path}")

    # Combine all results into final dataframe
    if full_results:
        final_df = pd.concat(full_results, ignore_index=True)
        print(f"\n✅ Collected {len(final_df):,} SERP results from {len(search_queries)} queries")
        return final_df
    else:
        print("\n⚠️  No results returned from any query")
        return None
