"""
SERP Results Collection Module
================================

Collects search engine results from Google via Bright Data SERP API.
Handles pagination, retries, error recovery, and per-query timeouts.

Features:
- Concurrent query execution (SERP_MAX_WORKERS threads, default: 5)
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm

from config import config


def _collect_single_query(query: str, max_pages: int, proxies: dict) -> dict:
    """
    Collect paginated SERP results for a single query.

    Returns a dict with keys: 'results' (list of DataFrames), 'failed' (dict or None).
    """
    current_url = query
    page_count = 0
    query_results = []
    query_timed_out = False
    query_start_time = time.time()

    while current_url and page_count < max_pages:
        elapsed = time.time() - query_start_time
        if elapsed > config.SERP_QUERY_TIMEOUT:
            query_timed_out = True
            break

        success = False

        for attempt in range(config.SERP_RETRY_ATTEMPTS):
            if time.time() - query_start_time > config.SERP_QUERY_TIMEOUT:
                query_timed_out = True
                break
            try:
                remaining = max(1, config.SERP_QUERY_TIMEOUT - (time.time() - query_start_time))
                request_timeout = min(config.SERP_TIMEOUT, remaining)

                response = requests.get(
                    current_url,
                    proxies=proxies,
                    timeout=request_timeout,
                    verify=False
                )
                response.raise_for_status()

                try:
                    parsed = json.loads(response.text)
                except json.JSONDecodeError:
                    if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                        time.sleep(2 ** attempt)
                        continue
                    parsed = {"organic": []}

                if not parsed.get("organic"):
                    break

                df = pd.DataFrame(parsed["organic"])
                required_columns = ["title", "description", "link", "rank"]
                for col in required_columns:
                    if col not in df.columns:
                        df[col] = None

                data = df[required_columns]
                data["query"] = parsed["general"]["query"]
                query_results.append(data)

                pagination = parsed.get("pagination", {})
                next_page_link = pagination.get("next_page_link") if pagination else None
                current_url = next_page_link + "&brd_json=1" if next_page_link else None

                page_count += 1
                success = True
                break

            except requests.exceptions.Timeout:
                if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
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
                    break

            except requests.exceptions.RequestException:
                if attempt < config.SERP_RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break

            except Exception:
                break

        if query_timed_out:
            break

        if not success and page_count == 0:
            return {"results": [], "failed": {"query": query, "reason": "request_failed"}, "pages": 0}

    if query_timed_out and page_count == 0:
        return {"results": [], "failed": {"query": query, "reason": "timed_out"}, "pages": 0}

    return {"results": query_results, "failed": None, "pages": page_count}


def _test_proxy_connectivity(proxies: dict):
    """Test proxy connectivity with retries. Raises RuntimeError on failure."""
    proxy_test_attempts = 3
    for attempt in range(proxy_test_attempts):
        try:
            test_response = requests.get(
                "https://www.google.com/search?q=test&brd_json=1",
                proxies=proxies,
                timeout=20,
                verify=False
            )
            print(f"DEBUG - Proxy test successful! Status code: {test_response.status_code}")
            return
        except Exception as test_error:
            wait = 2 ** attempt
            print(f"⚠️  Proxy test attempt {attempt + 1}/{proxy_test_attempts} failed: "
                  f"{type(test_error).__name__}: {str(test_error)[:200]}")
            if attempt < proxy_test_attempts - 1:
                print(f"    Retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError(
        "Proxy connectivity test failed after all retries. "
        "Check your Bright Data credentials and network connection before retrying."
    )


def collect_search_results(search_queries: List[str], max_pages: int = None) -> Optional[pd.DataFrame]:
    """
    Collect search results from multiple queries concurrently with pagination and retry logic.

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

    if not config.BRIGHT_DATA_PROXY_URL_HTTP or not config.BRIGHT_DATA_PROXY_URL_HTTPS:
        raise ValueError(
            "Bright Data proxy URLs are not configured. "
            "Set BRIGHT_DATA_PROXY_URL or BRIGHT_DATA_PROXY_URL_HTTP/HTTPS in the environment."
        )

    proxies = {
        'http': config.BRIGHT_DATA_PROXY_URL_HTTP,
        'https': config.BRIGHT_DATA_PROXY_URL_HTTPS
    }

    http_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTP).netloc
    https_host = urlparse(config.BRIGHT_DATA_PROXY_URL_HTTPS).netloc
    print(f"Using Bright Data proxy hosts: http={http_host}, https={https_host}")

    _test_proxy_connectivity(proxies)

    max_workers = config.SERP_MAX_WORKERS
    print(f"Collecting SERP results with {max_workers} concurrent workers...")

    full_results = []
    failed_queries = []
    results_lock = Lock()

    pbar = tqdm(
        total=len(search_queries),
        desc="Collecting SERP Results",
        unit="query",
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_query = {
            executor.submit(_collect_single_query, query, max_pages, proxies): query
            for query in search_queries
        }

        for future in as_completed(future_to_query):
            result = future.result()

            with results_lock:
                if result["results"]:
                    full_results.extend(result["results"])
                if result["failed"]:
                    failed_queries.append(result["failed"])

            pbar.set_postfix(
                results=sum(len(df) for df in full_results)
            )
            pbar.update(1)

    pbar.close()

    # Report and save failed queries
    if failed_queries:
        print(f"\n⚠️  {len(failed_queries)} queries failed completely:")
        for fq in failed_queries[:5]:
            print(f"   - [{fq['reason']}] {fq['query'][:100]}...")
        if len(failed_queries) > 5:
            print(f"   ... and {len(failed_queries) - 5} more")

        failed_df = pd.DataFrame(failed_queries)
        failed_path = config.SERP_FAILED_QUERIES_FILE
        write_header = not failed_path.exists()
        failed_df.to_csv(failed_path, mode='a', index=False, header=write_header)
        print(f"   Saved to {failed_path}")

    if full_results:
        final_df = pd.concat(full_results, ignore_index=True)
        print(f"\n✅ Collected {len(final_df):,} SERP results from {len(search_queries)} queries")
        return final_df
    else:
        print("\n⚠️  No results returned from any query")
        return None
