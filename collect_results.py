"""
SERP Results Collection Module
================================

Collects search engine results from Google via the Bright Data SERP API proxy.
This is the module that actually talks to Google (through Bright Data's proxy)
and returns structured search results.

Features:
  - Concurrent query execution (SERP_MAX_WORKERS threads, default 5)
  - Configurable pagination depth (MAX_SERP_PAGES, default 2 pages per query)
  - Per-query wall-clock timeout (SERP_QUERY_TIMEOUT, default 20s) -- skips
    queries that hang without blocking other active queries
  - Automatic retry with exponential backoff for transient failures
  - Rate limit (HTTP 429) handling with longer backoff
  - Progress tracking with tqdm progress bar
  - Failed queries logged to outputs/serp_failed_queries.csv for later retry

How it works:
  1. Validates Bright Data proxy connectivity with a test request
  2. Submits all queries to a ThreadPoolExecutor for concurrent processing
  3. Each query fetches up to MAX_SERP_PAGES pages of results
  4. Results are parsed from Bright Data's JSON response format
  5. Failed/timed-out queries are saved to CSV for manual retry

Input:
  List of Google search query URLs (from generate_queries.py)

Output:
  DataFrame with columns: title, description, link, rank, query
  (or None if no results were collected)
"""

import json
import time
from urllib.parse import urlparse
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import requests
import requests.packages.urllib3
from tqdm import tqdm

from config import config

# Suppress SSL warnings from the proxy (Bright Data uses its own certs)
requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)


# ---------------------------------------------------------------------------
# Single-query collection (called by each worker thread)
# ---------------------------------------------------------------------------

def _collect_single_query(query: str, max_pages: int, proxies: dict) -> dict:
    """
    Collect paginated SERP results for a single query.

    Fetches up to max_pages pages of Google results through the Bright Data
    proxy. Each page typically contains ~10 organic results.

    Implements:
      - Per-query wall-clock timeout (SERP_QUERY_TIMEOUT): if the total
        time for all pages + retries exceeds this, the query is abandoned.
      - Per-request retry with exponential backoff (SERP_RETRY_ATTEMPTS).
      - Rate limit handling: HTTP 429 responses trigger a longer backoff.

    Args:
        query:     Google search URL (with brd_json=1 parameter).
        max_pages: Maximum number of result pages to fetch.
        proxies:   Dict with 'http' and 'https' proxy URLs.

    Returns:
        Dict with keys:
          'results': list of DataFrames (one per page)
          'failed':  dict with {query, reason} if the query failed, else None
          'pages':   number of pages successfully fetched
    """
    current_url = query
    page_count = 0
    query_results = []
    query_timed_out = False
    query_start_time = time.time()

    # -- Paginate through results --
    while current_url and page_count < max_pages:
        # Check if we've exceeded the per-query timeout
        elapsed = time.time() - query_start_time
        if elapsed > config.SERP_QUERY_TIMEOUT:
            query_timed_out = True
            break

        success = False

        # -- Retry loop for each page --
        for attempt in range(config.SERP_RETRY_ATTEMPTS):
            # Re-check timeout before each retry attempt
            if time.time() - query_start_time > config.SERP_QUERY_TIMEOUT:
                query_timed_out = True
                break

            try:
                # Calculate remaining time budget for this request
                remaining = max(
                    1,
                    config.SERP_QUERY_TIMEOUT - (time.time() - query_start_time)
                )
                request_timeout = min(config.SERP_TIMEOUT, remaining)

                # Send the request through the Bright Data proxy
                response = requests.get(
                    current_url,
                    proxies=proxies,
                    timeout=request_timeout,
                    verify=False  # Bright Data proxy uses its own SSL certs
                )
                response.raise_for_status()

                # Parse the JSON response from Bright Data
                try:
                    parsed = json.loads(response.text)
                except json.JSONDecodeError:
                    # Bad JSON -- retry if we have attempts left
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)
                        continue
                    parsed = {"organic": []}

                # No organic results = end of results for this query
                if not parsed.get("organic"):
                    break

                # Extract organic results into a DataFrame
                df = pd.DataFrame(parsed["organic"])

                # Ensure all required columns exist (some may be missing)
                required_columns = ["title", "description", "link", "rank"]
                for col in required_columns:
                    if col not in df.columns:
                        df[col] = None

                # Keep only the columns we need and add the query URL
                data = df[required_columns]
                data["query"] = parsed["general"]["query"]
                query_results.append(data)

                # Check for next page link in the pagination object
                pagination = parsed.get("pagination", {})
                next_page_link = (
                    pagination.get("next_page_link") if pagination else None
                )
                # Append brd_json=1 to the next page URL so Bright Data
                # returns JSON instead of HTML
                current_url = (
                    next_page_link + "&brd_json=1" if next_page_link else None
                )

                page_count += 1
                success = True
                break  # Success -- exit retry loop

            except requests.exceptions.Timeout:
                # Request timed out -- retry with backoff
                if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break

            except requests.exceptions.HTTPError as e:
                status_code = (
                    e.response.status_code if e.response is not None else None
                )
                if status_code == 429 and attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    # Rate limited -- use a longer backoff
                    time.sleep(10 * (attempt + 1))
                    continue
                if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break

            except requests.exceptions.RequestException:
                # Network error -- retry with backoff
                if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break

            except Exception:
                # Unexpected error -- don't retry
                break

        # If query timed out, stop paginating
        if query_timed_out:
            break

        # If first page failed with no results, the query is a total failure
        if not success and page_count == 0:
            return {
                "results": [],
                "failed": {"query": query, "reason": "request_failed"},
                "pages": 0
            }

    # If query timed out before getting any results, it's a total failure
    if query_timed_out and page_count == 0:
        return {
            "results": [],
            "failed": {"query": query, "reason": "timed_out"},
            "pages": 0
        }

    return {"results": query_results, "failed": None, "pages": page_count}


# ---------------------------------------------------------------------------
# Proxy connectivity test
# ---------------------------------------------------------------------------

def _test_proxy_connectivity(proxies: dict):
    """
    Test that the Bright Data proxy is reachable and working.

    Makes a simple test request through the proxy. Retries 3 times with
    exponential backoff before giving up.

    Raises:
        RuntimeError: If all test attempts fail.
    """
    proxy_test_attempts = 3

    for attempt in range(proxy_test_attempts):
        try:
            test_response = requests.get(
                "https://www.google.com/search?q=test&brd_json=1",
                proxies=proxies,
                timeout=45,
                verify=False
            )
            print(f"Proxy test OK (status {test_response.status_code})")
            return  # Success!
        except Exception as test_error:
            wait = 2 ** attempt
            print(f"Proxy test attempt {attempt + 1}/{proxy_test_attempts} "
                  f"failed: {type(test_error).__name__}: "
                  f"{str(test_error)[:200]}")
            if attempt < proxy_test_attempts - 1:
                print(f"    Retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError(
        "Proxy connectivity test failed after all retries. "
        "Check your Bright Data credentials and network connection."
    )


# ---------------------------------------------------------------------------
# Main collection function (public API)
# ---------------------------------------------------------------------------

def collect_search_results(search_queries: List[str],
                           max_pages: int = None) -> Optional[pd.DataFrame]:
    """
    Collect SERP results from multiple queries concurrently.

    This is the main entry point for SERP collection. It:
      1. Validates proxy connectivity
      2. Submits all queries to a thread pool
      3. Collects results as they complete
      4. Reports and saves any failed queries

    Args:
        search_queries: List of Google search query URLs (from generate_queries.py).
        max_pages:      Maximum pages per query. Defaults to config.MAX_SERP_PAGES.

    Returns:
        Combined DataFrame with all results (columns: title, description,
        link, rank, query), or None if no results were collected.
    """
    if max_pages is None:
        max_pages = config.MAX_SERP_PAGES

    # Validate that proxy URLs are configured
    if not config.BRIGHT_DATA_PROXY_URL_HTTP or not config.BRIGHT_DATA_PROXY_URL_HTTPS:
        raise ValueError(
            "Bright Data proxy URLs are not configured. "
            "Set BRIGHT_DATA_PROXY_URL or BRIGHT_DATA_PROXY_URL_HTTP/HTTPS."
        )

    proxies = {
        'http': config.BRIGHT_DATA_PROXY_URL_HTTP,
        'https': config.BRIGHT_DATA_PROXY_URL_HTTPS
    }

    # Log proxy hosts (with credentials masked)
    http_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTP).netloc
    https_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTPS).netloc
    print(f"Using Bright Data proxy hosts: http={http_host}, https={https_host}")

    # Verify the proxy is reachable before sending real queries
    _test_proxy_connectivity(proxies)

    max_workers = config.SERP_MAX_WORKERS
    print(f"Collecting SERP results with {max_workers} concurrent workers...")

    # Thread-safe containers for results and failed queries
    full_results = []
    failed_queries = []
    results_lock = Lock()

    # Progress bar for user feedback
    pbar = tqdm(
        total=len(search_queries),
        desc="Collecting SERP Results",
        unit="query",
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
    )

    # -- Submit all queries to the thread pool --
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_query = {
            executor.submit(
                _collect_single_query, query, max_pages, proxies
            ): query
            for query in search_queries
        }

        # Process results as they complete
        for future in as_completed(future_to_query):
            result = future.result()

            with results_lock:
                if result["results"]:
                    full_results.extend(result["results"])
                if result["failed"]:
                    failed_queries.append(result["failed"])

            # Update progress bar with running total of results
            pbar.set_postfix(
                results=sum(len(df) for df in full_results)
            )
            pbar.update(1)

    pbar.close()

    # -- Report and save failed queries --
    if failed_queries:
        print(f"\n{len(failed_queries)} queries failed completely:")
        for fq in failed_queries[:5]:
            print(f"   - [{fq['reason']}] {fq['query'][:100]}...")
        if len(failed_queries) > 5:
            print(f"   ... and {len(failed_queries) - 5} more")

        # Append failed queries to CSV (append mode so we don't lose
        # failures from previous runs)
        failed_df = pd.DataFrame(failed_queries)
        failed_path = config.SERP_FAILED_QUERIES_FILE
        write_header = not failed_path.exists()
        failed_df.to_csv(
            failed_path, mode='a', index=False, header=write_header
        )
        print(f"   Saved to {failed_path}")

    # -- Combine all results into a single DataFrame --
    if full_results:
        final_df = pd.concat(full_results, ignore_index=True)
        print(f"\nCollected {len(final_df):,} SERP results "
              f"from {len(search_queries)} queries")
        return final_df
    else:
        print("\nNo results returned from any query")
        return None
