"""
Press Release Collection Pipeline -- Cloud Run HTTP Endpoint
==============================================================

Google Cloud Run function for collecting corporate press releases.
Designed to be STATELESS, SCALABLE, and IDEMPOTENT.

Daily Scheduling (no parameters needed):
  When called without start_date/end_date, the pipeline queries the
  collection_runs BigQuery table for the most recently completed run and
  automatically starts from that run's end_date (1-day overlap for safety).
  On the very first run, it defaults to a 7-day lookback.

HTTP API:
  POST /
  Body (all fields optional):
    {
        "start_date": "YYYY-MM-DD",    // auto-detected from last run if omitted
        "end_date": "YYYY-MM-DD",      // defaults to today if omitted
        "mode": "full",                 // "test" limits to 5 SERP queries
        "force_refresh": false,         // bypass all deduplication
        "skip_scraping": false          // SERP collection only, no scraping
    }

  Response:
    {
        "status": "success|error",
        "message": "...",
        "stats": {...},
        "run_id": "..."
    }

Environment Variables:
  BRIGHT_DATA_PROXY_URL:   Bright Data SERP proxy credentials
  BIGQUERY_DATASET:        Dataset name (default: pressure_monitoring)
  GCP_PROJECT:             Google Cloud project ID

Key Priority Metadata Collected:
  - company:      Identified by matching site:url in SERP query to reference data
  - sector:       From benchmarking_corporate_reference table
  - publish_date: Extracted by scrapers from the article content/URL
"""

import hashlib
import json
import re
import subprocess
import sys
import traceback
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

import functions_framework
import pandas as pd
from flask import Request

# Pipeline modules
from bigquery_storage import BigQueryStorage, _generate_press_release_id
from collect_results import collect_search_results
from config import config
from generate_queries import create_search_queries
from grab_reference_data import grab_reference_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_newsroom_from_query(query: str) -> str:
    """
    Extract the newsroom base URL from a SERP query string.

    Handles two formats:
      1. Full Google URL: https://www.google.com/search?q=site:https://...
      2. Raw query text:  site:https://about.att.com/story/ before:2026-03-17

    Returns:
      The site: URL (e.g. 'https://newsroom.apple.com'), or '' on failure.
    """
    # Try as full URL first (has ?q= parameter)
    try:
        parsed = urllib.parse.urlparse(query)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            q_param = urllib.parse.parse_qs(parsed.query).get('q', [''])[0]
            match = re.match(r'site:(\S+)', q_param)
            if match:
                return match.group(1).rstrip('/')
    except Exception:
        pass
    # Fallback: raw query text (site:url before:... after:...)
    match = re.match(r'site:(\S+)', query.strip())
    if match:
        return match.group(1).rstrip('/')
    return ''


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def validate_request(request_json: Dict) -> Tuple[bool, str, Dict]:
    """
    Validate incoming HTTP request parameters.

    Expects start_date and end_date to ALREADY be set in request_json
    (the main endpoint fills them in via BigQuery auto-detection before
    calling this function).

    Returns:
        (is_valid, error_message, validated_params)
    """
    # Validate mode parameter
    mode = request_json.get('mode', 'full')
    if mode not in ('test', 'full'):
        return False, f"Invalid mode '{mode}'. Must be 'test' or 'full'", {}

    params = {
        'start_date': request_json.get('start_date'),
        'end_date': request_json.get('end_date'),
        'force_refresh': request_json.get('force_refresh', False),
        'skip_scraping': request_json.get('skip_scraping', False),
        'mode': mode,
    }

    # start_date and end_date should have been filled in by the caller
    if not params['start_date'] or not params['end_date']:
        return False, "start_date and end_date must be set", {}

    # Validate date format
    try:
        datetime.strptime(params['start_date'], '%Y-%m-%d')
        datetime.strptime(params['end_date'], '%Y-%m-%d')
    except ValueError:
        return False, "Invalid date format. Use YYYY-MM-DD", {}

    # Validate date order
    if params['start_date'] > params['end_date']:
        return False, "start_date must be before or equal to end_date", {}

    return True, "", params


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def run_serp_collection(
    start_date: str,
    end_date: str,
    force_refresh: bool,
    run_id: str,
    storage: BigQueryStorage,
    test_mode: bool = False,
) -> Tuple[Dict[str, Any], Optional[pd.DataFrame]]:
    """
    Execute SERP collection with query-level and URL-level deduplication.

    This is the core SERP stage. It applies two layers of deduplication
    (cheapest checks first) to minimize SERP API costs:

      Layer 1 - Query-level dedup:
        Skip SERP queries already executed for overlapping date ranges.
        This is the cheapest check because it avoids the SERP API entirely.

      Layer 2 - URL-level dedup:
        Filter SERP results against press_release_ids already in BigQuery.
        This prevents duplicate rows when the same article appears in
        multiple queries or overlapping date ranges.

    Also handles:
      - Newsroom backfill (detects new companies and backfills from 2026-01-01)
      - Company/sector annotation (maps newsroom_url -> company + sector)
      - Test mode (limits to 5 queries to conserve SERP credits)

    Args:
        start_date:    Start date for SERP queries (YYYY-MM-DD).
        end_date:      End date for SERP queries (YYYY-MM-DD).
        force_refresh: If True, skip all deduplication.
        run_id:        Unique run identifier for logging.
        storage:       BigQueryStorage instance.
        test_mode:     If True, limit to 5 queries.

    Returns:
        (stats_dict, serp_dataframe_or_None)
        The DataFrame contains only NEW results not yet in BigQuery,
        with press_release_id, company, sector, and newsroom_url added.
    """
    stats: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1: Fetch reference data (company, sector, newsroom_url)
    # ------------------------------------------------------------------
    print(f"[{run_id}] Fetching reference data...")
    reference_df = grab_reference_data()

    if reference_df.empty:
        raise ValueError("No reference data available")

    stats['companies_count'] = len(reference_df)

    # Extract company list for logging
    companies = (
        reference_df['corporation'].tolist()
        if 'corporation' in reference_df.columns
        else []
    )

    # Build lookup dicts: newsroom_url -> company name, newsroom_url -> sector
    # These are used later to annotate each SERP result with company/sector.
    newsroom_to_company: Dict[str, str] = {}
    newsroom_to_sector: Dict[str, str] = {}
    if 'newsroom_url' in reference_df.columns:
        for _, row in reference_df.iterrows():
            newsroom = str(row.get('newsroom_url') or '').strip().rstrip('/')
            corp = str(row.get('corporation') or '').strip()
            sector = str(row.get('sector') or '').strip()
            if newsroom:
                newsroom_to_company[newsroom] = corp
                newsroom_to_sector[newsroom] = sector

    # ------------------------------------------------------------------
    # Step 2: Detect new newsrooms needing historical backfill
    # ------------------------------------------------------------------
    # When a new company is added to the reference data, we want to
    # backfill its press releases from a fixed historical start date,
    # not just from this run's start_date.
    print(f"[{run_id}] Checking for new newsrooms needing backfill...")

    current_newsroom_urls = []
    if 'newsroom_url' in reference_df.columns:
        current_newsroom_urls = [
            str(u).strip()
            for u in reference_df['newsroom_url'].dropna().tolist()
        ]

    backfill_start_date = "2026-01-01"
    if not force_refresh:
        # Compare current newsroom URLs against ones we've already collected
        collected_newsrooms = storage.get_collected_newsroom_urls()
        new_newsrooms = set(current_newsroom_urls) - collected_newsrooms
        if new_newsrooms:
            print(f"[{run_id}] {len(new_newsrooms)} new newsrooms -- "
                  f"backfilling from {backfill_start_date}")
            effective_start_date = backfill_start_date
            stats['backfill_urls_count'] = len(new_newsrooms)
        else:
            print(f"[{run_id}] No new newsrooms")
            effective_start_date = start_date
            stats['backfill_urls_count'] = 0
    else:
        effective_start_date = start_date
        stats['backfill_urls_count'] = 0

    # ------------------------------------------------------------------
    # Step 3: Load existing IDs for URL-level deduplication
    # ------------------------------------------------------------------
    existing_ids: set = set()
    if not force_refresh:
        print(f"[{run_id}] Loading existing press release IDs from BigQuery...")
        existing_ids = storage.get_existing_press_release_ids()
    else:
        print(f"[{run_id}] Force refresh -- skipping ID-level dedup")

    # ------------------------------------------------------------------
    # Step 4: Generate SERP queries
    # ------------------------------------------------------------------
    print(f"[{run_id}] Generating queries for "
          f"{effective_start_date} -> {end_date}...")
    reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)
    all_queries = create_search_queries(
        start_date=effective_start_date, end_date=end_date
    )
    stats['queries_generated'] = len(all_queries)

    # ------------------------------------------------------------------
    # Step 5: Query-level deduplication (cheapest check -- saves API $)
    # ------------------------------------------------------------------
    # Check which queries have already been executed in recent completed
    # runs for overlapping date ranges. Skip those entirely.
    queries_to_execute = all_queries
    if not force_refresh:
        print(f"[{run_id}] Checking for already-executed queries...")
        already_executed = storage.get_executed_queries_for_date_range(
            start_date=effective_start_date, end_date=end_date
        )
        if already_executed:
            queries_to_execute = [
                q for q in all_queries if q not in already_executed
            ]
            skipped = len(all_queries) - len(queries_to_execute)
            print(f"[{run_id}] Skipped {skipped:,} already-executed queries "
                  f"(saves SERP API costs)")
            stats['queries_skipped'] = skipped
        else:
            stats['queries_skipped'] = 0
    else:
        stats['queries_skipped'] = 0

    # ------------------------------------------------------------------
    # Step 5b: Test mode -- limit queries to save SERP credits
    # ------------------------------------------------------------------
    if test_mode and len(queries_to_execute) > 5:
        print(f"[{run_id}] TEST MODE -- limiting to 5 of "
              f"{len(queries_to_execute):,} queries")
        queries_to_execute = queries_to_execute[:5]

    stats['queries_executed'] = len(queries_to_execute)
    stats['all_queries'] = queries_to_execute  # Stored for run log

    if not queries_to_execute:
        print(f"[{run_id}] All queries already executed -- nothing to collect")
        stats['serp_results_count'] = 0
        return stats, None

    # ------------------------------------------------------------------
    # Step 6: Collect SERP results via Bright Data proxy
    # ------------------------------------------------------------------
    # Use more pages for backfill runs (10) vs regular runs (config default)
    is_backfill = effective_start_date != start_date
    max_pages = 10 if is_backfill else config.MAX_SERP_PAGES
    print(f"[{run_id}] Collecting SERP results for "
          f"{len(queries_to_execute):,} queries "
          f"(max_pages={max_pages}"
          f"{'  [backfill]' if is_backfill else ''})...")
    serp_df = collect_search_results(
        search_queries=queries_to_execute, max_pages=max_pages
    )

    if serp_df is None or serp_df.empty:
        print(f"[{run_id}] No SERP results returned")
        stats['serp_results_count'] = 0
        return stats, None

    # Normalize column name (SERP module uses 'link', we use 'url')
    if 'link' in serp_df.columns:
        serp_df = serp_df.rename(columns={'link': 'url'})

    # ------------------------------------------------------------------
    # Step 7: Annotate with press_release_id, company, sector, newsroom_url
    # ------------------------------------------------------------------
    # For each SERP result, figure out which company it belongs to by
    # extracting the site:url from the query and looking it up in our
    # reference data mapping.
    serp_df['press_release_id'] = serp_df['url'].apply(
        _generate_press_release_id
    )
    serp_df['newsroom_url'] = serp_df['query'].apply(
        _extract_newsroom_from_query
    )
    serp_df['company'] = serp_df['newsroom_url'].map(
        newsroom_to_company
    ).fillna('')
    serp_df['sector'] = serp_df['newsroom_url'].map(
        newsroom_to_sector
    ).fillna('')

    # ------------------------------------------------------------------
    # Step 8: URL-level deduplication against BigQuery
    # ------------------------------------------------------------------
    # Remove articles we've already scraped (by press_release_id).
    if existing_ids:
        pre_dedup = len(serp_df)
        serp_df = serp_df[~serp_df['press_release_id'].isin(existing_ids)]
        url_dupes = pre_dedup - len(serp_df)
        if url_dupes:
            print(f"[{run_id}] Removed {url_dupes:,} URLs already in BigQuery")

    # Also deduplicate within this batch (same URL from multiple queries)
    serp_df = serp_df.drop_duplicates(subset=['press_release_id'], keep='first')

    stats['serp_results_count'] = len(serp_df)

    if serp_df.empty:
        print(f"[{run_id}] No new URLs after deduplication")
        return stats, None

    # ------------------------------------------------------------------
    # Step 9: Save to CSV and write metadata to BigQuery
    # ------------------------------------------------------------------
    # Save CSV (article_scraper.py reads this file)
    print(f"[{run_id}] Saving {len(serp_df):,} new SERP results...")
    serp_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)

    # Write metadata to BigQuery IMMEDIATELY (before scraping) so results
    # survive even if the scraping stage crashes or times out.
    n_written = storage.write_press_release_metadata(serp_df, run_id=run_id)
    print(f"[{run_id}] Wrote {n_written} metadata rows to BigQuery")

    return stats, serp_df


def run_article_scraping(run_id: str, storage: BigQueryStorage) -> Dict[str, Any]:
    """
    Execute article scraping and write results to BigQuery.

    Runs article_scraper.py as a subprocess (isolates heavy dependencies).
    After scraping completes, reads the joined CSV and writes:
      - press_release_metadata  (updates with scraped titles)
      - press_release_content   (article text for successfully scraped URLs)
      - publish_date updates    (backfills dates extracted by scrapers)

    Args:
        run_id:  Unique run identifier for logging.
        storage: BigQueryStorage instance.

    Returns:
        Stats dictionary with 'articles_scraped' count.
    """
    stats: Dict[str, Any] = {}

    print(f"[{run_id}] Launching article scraper...")

    # Run scraper as subprocess with 1-hour timeout
    result = subprocess.run(
        [sys.executable, "-u", "article_scraper.py"],
        timeout=3600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Article scraper exited with code {result.returncode}"
        )

    # ------------------------------------------------------------------
    # Write scraped data to BigQuery from the joined CSV
    # ------------------------------------------------------------------
    if config.JOINED_RESULTS_FILE.exists():
        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)
        stats['articles_scraped'] = int(
            joined_df['article_text'].notna().sum()
        )

        if not joined_df.empty:
            # Update metadata with better scraped titles
            storage.write_press_release_metadata(joined_df, run_id=run_id)

            # Backfill publish_date from scraped data
            storage.update_metadata_publish_dates(joined_df)

            # Write article content (only rows where scraping succeeded)
            content_df = joined_df[joined_df['article_text'].notna()].copy()
            if not content_df.empty:
                storage.write_press_release_content(content_df, run_id=run_id)
    else:
        print(f"[{run_id}] {config.JOINED_RESULTS_FILE} not found -- "
              f"no content written")
        stats['articles_scraped'] = 0

    return stats


# ---------------------------------------------------------------------------
# Cloud Run entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def press_release_collection(request: Request):
    """
    Cloud Run HTTP endpoint for press release collection.

    Daily usage (no body needed -- dates are auto-detected):
        curl -X POST $SERVICE_URL

    Manual date range:
        curl -X POST $SERVICE_URL \\
          -H 'Content-Type: application/json' \\
          -d '{"start_date": "2026-03-01", "end_date": "2026-03-04"}'

    Test mode (5 queries only):
        curl -X POST $SERVICE_URL \\
          -H 'Content-Type: application/json' \\
          -d '{"mode": "test"}'
    """
    run_id = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    response: Dict[str, Any] = {
        'status': 'error',
        'message': '',
        'run_id': run_id,
        'stats': {},
        'timestamp': datetime.utcnow().isoformat(),
    }

    try:
        request_json: Dict = request.get_json(silent=True) or {}
        print(f"[{run_id}] Starting pipeline with params: {request_json}")

        # ------------------------------------------------------------------
        # Initialize BigQuery (needed for date auto-detection)
        # ------------------------------------------------------------------
        storage = BigQueryStorage()
        storage.initialize_tables()

        # ------------------------------------------------------------------
        # Determine date range
        # ------------------------------------------------------------------
        # If the caller didn't provide start_date, auto-detect from
        # the last successful run in BigQuery. This is what makes
        # Cloud Run "zero-config" -- it just picks up where it left off.
        today = datetime.utcnow().date()

        if 'start_date' not in request_json:
            last_end_date = storage.get_last_successful_run_end_date()
            if last_end_date:
                # Start from last run's end_date (1-day overlap for safety)
                auto_start = last_end_date
                print(f"[{run_id}] Auto start_date: {auto_start} "
                      f"(last run's end_date)")
            else:
                # First ever run: default 7-day lookback
                auto_start = (today - timedelta(days=7)).strftime('%Y-%m-%d')
                print(f"[{run_id}] No prior runs -- "
                      f"defaulting to 7-day lookback: {auto_start}")
            request_json['start_date'] = auto_start

        if 'end_date' not in request_json:
            request_json['end_date'] = today.strftime('%Y-%m-%d')
            print(f"[{run_id}] Auto end_date: {request_json['end_date']} (today)")

        # ------------------------------------------------------------------
        # Validate request parameters
        # ------------------------------------------------------------------
        is_valid, error_msg, params = validate_request(request_json)
        if not is_valid:
            response['message'] = error_msg
            return json.dumps(response), 400

        print(f"[{run_id}] Date range: "
              f"{params['start_date']} -> {params['end_date']}")

        # ------------------------------------------------------------------
        # Log run start
        # ------------------------------------------------------------------
        reference_df = grab_reference_data()
        companies = (
            reference_df['corporation'].tolist()
            if 'corporation' in reference_df.columns
            else []
        )

        storage.log_run_start(
            run_id=run_id,
            start_date=params['start_date'],
            end_date=params['end_date'],
            companies=companies[:100],
        )

        # ------------------------------------------------------------------
        # SERP collection (with query-level and URL-level dedup)
        # ------------------------------------------------------------------
        is_test = params['mode'] == 'test'
        if is_test:
            print(f"[{run_id}] Running in TEST mode (max 5 SERP queries)")

        serp_stats, serp_df = run_serp_collection(
            start_date=params['start_date'],
            end_date=params['end_date'],
            force_refresh=params['force_refresh'],
            run_id=run_id,
            storage=storage,
            test_mode=is_test,
        )
        response['stats']['mode'] = params['mode']
        response['stats'].update({
            k: v for k, v in serp_stats.items() if k != 'all_queries'
        })

        # ------------------------------------------------------------------
        # Article scraping (or metadata-only write)
        # ------------------------------------------------------------------
        if serp_df is not None and not serp_df.empty:
            if params['skip_scraping']:
                # No scraping -- just write SERP metadata
                storage.write_press_release_metadata(serp_df, run_id=run_id)
                response['stats']['articles_scraped'] = 0
                print(f"[{run_id}] Scraping skipped -- "
                      f"wrote {len(serp_df):,} metadata rows only")
            else:
                # Run the full scraping pipeline
                scraping_stats = run_article_scraping(
                    run_id=run_id, storage=storage
                )
                response['stats'].update(scraping_stats)
        else:
            response['stats']['articles_scraped'] = 0

        # ------------------------------------------------------------------
        # Log run completion
        # ------------------------------------------------------------------
        storage.log_run_completion(
            run_id=run_id,
            urls_collected=response['stats'].get('serp_results_count', 0),
            articles_scraped=response['stats'].get('articles_scraped', 0),
            queries_executed=serp_stats.get('all_queries', []),
            error_message=None,
        )

        response['status'] = 'success'
        response['message'] = (
            f"Pipeline completed. "
            f"{response['stats'].get('serp_results_count', 0)} new URLs, "
            f"{response['stats'].get('articles_scraped', 0)} articles scraped."
        )
        print(f"[{run_id}] Pipeline completed: {response['stats']}")
        return json.dumps(response), 200

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"[{run_id}] Pipeline failed:\n{error_trace}")

        response['status'] = 'error'
        response['message'] = f"{type(e).__name__}: {str(e)}"
        response['error_trace'] = error_trace

        # Best-effort failure log to BigQuery
        try:
            if 'storage' in locals():
                storage.log_run_completion(
                    run_id=run_id,
                    urls_collected=response['stats'].get(
                        'serp_results_count', 0
                    ),
                    articles_scraped=response['stats'].get(
                        'articles_scraped', 0
                    ),
                    queries_executed=[],
                    error_message=str(e),
                )
        except Exception:
            pass

        return json.dumps(response), 500


# ---------------------------------------------------------------------------
# Local test server
# ---------------------------------------------------------------------------
# When run directly (not via Cloud Run), starts a local Flask server
# so you can test the Cloud Run endpoint locally.

if __name__ == "__main__":
    from flask import Flask, request as flask_request

    app = Flask(__name__)

    @app.route('/', methods=['POST'])
    def local_handler():
        return press_release_collection(flask_request)

    print("Local test server: http://localhost:8080")
    print("No-param daily run:  curl -X POST http://localhost:8080")
    print("Explicit dates:      curl -X POST http://localhost:8080 "
          "-H 'Content-Type: application/json' "
          "-d '{\"start_date\": \"2026-03-01\", \"end_date\": \"2026-03-04\"}'")
    app.run(host='0.0.0.0', port=8080, debug=True)
