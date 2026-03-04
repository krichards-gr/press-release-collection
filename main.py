"""
Press Release Collection Pipeline - Cloud Run HTTP Endpoint
============================================================

Google Cloud Run function for collecting and processing corporate press releases.
Designed to be stateless, scalable, and idempotent.

Daily Scheduling (no parameters needed):
    When called without start_date/end_date, the pipeline queries the
    collection_runs BigQuery table for the most recently completed run and
    automatically starts from that run's end_date (1-day overlap for safety).
    On the very first run, it defaults to a 7-day lookback.

HTTP API:
    POST /
    Body: {
        "start_date": "YYYY-MM-DD",   // optional — auto-detected from last run
        "end_date": "YYYY-MM-DD",     // optional — defaults to today
        "force_refresh": false,
        "skip_scraping": false
    }

    Response: {
        "status": "success|error",
        "message": "...",
        "stats": {...},
        "run_id": "..."
    }

Environment Variables:
    - BRIGHT_DATA_PROXY_URL:    Bright Data proxy credentials
    - BIGQUERY_DATASET:         Dataset name (default: pressure_monitoring)
    - GCP_PROJECT:              Google Cloud project ID
"""

import hashlib
import json
import os
import re
import traceback
import urllib.parse
from datetime import datetime, timedelta, date
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
    Extract the newsroom base URL from a Google SERP query string.

    Query format:
        https://www.google.com/search?q=site:newsroom.apple.com+before:...+after:...

    Returns the site: host (e.g. 'newsroom.apple.com'), or '' on failure.
    """
    try:
        parsed = urllib.parse.urlparse(query)
        q_param = urllib.parse.parse_qs(parsed.query).get('q', [''])[0]
        # parse_qs decodes '+' as space, so value looks like:
        # "site:newsroom.apple.com before:2026-03-04 after:2026-03-03"
        match = re.match(r'site:(\S+)', q_param)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def validate_request(request_json: Dict) -> Tuple[bool, str, Dict]:
    """
    Validate incoming request parameters.

    Note: start_date and end_date are expected to already be set in
    request_json before this is called (set by press_release_collection
    using BigQuery auto-detection when not provided by the caller).

    Returns:
        (is_valid, error_message, validated_params)
    """
    params = {
        'start_date': request_json.get('start_date'),
        'end_date': request_json.get('end_date'),
        'force_refresh': request_json.get('force_refresh', False),
        'skip_scraping': request_json.get('skip_scraping', False),
    }

    if not params['start_date'] or not params['end_date']:
        return False, "start_date and end_date must be set before calling validate_request", {}

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
) -> Tuple[Dict[str, Any], Optional[pd.DataFrame]]:
    """
    Execute SERP collection with query-level and URL-level deduplication.

    Deduplication layers (in order, cheapest first):
        1. Query-level:   skip queries already executed for this date range
                          (saves SERP API costs entirely)
        2. URL-level:     filter SERP results against press_release_ids already
                          in BigQuery (prevents duplicate BQ rows on overlapping
                          date ranges or re-runs)

    Returns:
        (stats dict, serp_df or None)
        serp_df contains only *new* results not yet in BigQuery,
        with press_release_id, company, and newsroom_url columns added.
    """
    stats: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1: Reference data
    # ------------------------------------------------------------------
    print(f"[{run_id}] Fetching reference data...")
    reference_df = grab_reference_data()

    if reference_df.empty:
        raise ValueError("No reference data available")

    stats['companies_count'] = len(reference_df)

    if 'corporation' in reference_df.columns:
        companies = reference_df['corporation'].tolist()
    elif 'Company' in reference_df.columns:
        companies = reference_df['Company'].tolist()
    else:
        companies = []

    # Build newsroom_url → company name mapping for later annotation
    newsroom_to_company: Dict[str, str] = {}
    if 'newsroom_url' in reference_df.columns and 'corporation' in reference_df.columns:
        for _, row in reference_df.iterrows():
            newsroom = str(row.get('newsroom_url') or '').strip()
            corp = str(row.get('corporation') or '').strip()
            if newsroom:
                newsroom_to_company[newsroom] = corp

    # ------------------------------------------------------------------
    # Step 2: Detect newsrooms needing historical backfill
    # ------------------------------------------------------------------
    print(f"[{run_id}] Checking for new newsrooms needing backfill...")

    current_newsroom_urls = []
    if 'newsroom_url' in reference_df.columns:
        current_newsroom_urls = [
            str(u).strip() for u in reference_df['newsroom_url'].dropna().tolist()
        ]

    backfill_start_date = "2026-01-01"
    if not force_refresh:
        # Compare against newsrooms we have actually collected before
        collected_newsrooms = storage.get_collected_newsroom_urls()
        new_newsrooms = set(current_newsroom_urls) - collected_newsrooms
        if new_newsrooms:
            print(f"[{run_id}] 🆕 {len(new_newsrooms)} new newsrooms — backfilling from {backfill_start_date}")
            effective_start_date = backfill_start_date
            stats['backfill_urls_count'] = len(new_newsrooms)
        else:
            print(f"[{run_id}] ✓ No new newsrooms")
            effective_start_date = start_date
            stats['backfill_urls_count'] = 0
    else:
        effective_start_date = start_date
        stats['backfill_urls_count'] = 0

    # ------------------------------------------------------------------
    # Step 3: Load existing press_release_ids for URL-level dedup
    # ------------------------------------------------------------------
    existing_ids: set = set()
    if not force_refresh:
        print(f"[{run_id}] Loading existing press release IDs from BigQuery...")
        existing_ids = storage.get_existing_press_release_ids()
    else:
        print(f"[{run_id}] ⚠️  Force refresh — skipping ID-level deduplication")

    # ------------------------------------------------------------------
    # Step 4: Generate queries
    # ------------------------------------------------------------------
    print(f"[{run_id}] Generating queries for {effective_start_date} → {end_date}...")
    reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)
    all_queries = create_search_queries(start_date=effective_start_date, end_date=end_date)
    stats['queries_generated'] = len(all_queries)

    # ------------------------------------------------------------------
    # Step 5: Query-level deduplication (cheapest check — avoids SERP API)
    # ------------------------------------------------------------------
    queries_to_execute = all_queries
    if not force_refresh:
        print(f"[{run_id}] Checking for already-executed queries...")
        already_executed = storage.get_executed_queries_for_date_range(
            start_date=effective_start_date, end_date=end_date
        )
        if already_executed:
            queries_to_execute = [q for q in all_queries if q not in already_executed]
            skipped = len(all_queries) - len(queries_to_execute)
            print(f"[{run_id}] 💰 Skipped {skipped:,} already-executed queries (saves SERP API costs)")
            stats['queries_skipped'] = skipped
        else:
            stats['queries_skipped'] = 0
    else:
        stats['queries_skipped'] = 0

    stats['queries_executed'] = len(queries_to_execute)
    stats['all_queries'] = queries_to_execute  # stored for run log

    if not queries_to_execute:
        print(f"[{run_id}] ✓ All queries already executed — nothing to collect")
        stats['serp_results_count'] = 0
        return stats, None

    # ------------------------------------------------------------------
    # Step 6: Collect SERP results
    # ------------------------------------------------------------------
    print(f"[{run_id}] Collecting SERP results for {len(queries_to_execute):,} queries...")
    serp_df = collect_search_results(search_queries=queries_to_execute)

    if serp_df is None or serp_df.empty:
        print(f"[{run_id}] No SERP results returned")
        stats['serp_results_count'] = 0
        return stats, None

    # Normalise column name
    if 'link' in serp_df.columns:
        serp_df = serp_df.rename(columns={'link': 'url'})

    # ------------------------------------------------------------------
    # Step 7: Annotate with press_release_id, company, newsroom_url
    # ------------------------------------------------------------------
    serp_df['press_release_id'] = serp_df['url'].apply(_generate_press_release_id)
    serp_df['newsroom_url'] = serp_df['query'].apply(_extract_newsroom_from_query)
    serp_df['company'] = serp_df['newsroom_url'].map(newsroom_to_company).fillna('')

    # ------------------------------------------------------------------
    # Step 8: URL-level deduplication against BigQuery
    # ------------------------------------------------------------------
    if existing_ids:
        pre_dedup = len(serp_df)
        serp_df = serp_df[~serp_df['press_release_id'].isin(existing_ids)]
        url_dupes = pre_dedup - len(serp_df)
        if url_dupes:
            print(f"[{run_id}] 💰 Removed {url_dupes:,} URLs already in BigQuery")

    # Deduplicate within this batch (same URL from multiple queries / pages)
    serp_df = serp_df.drop_duplicates(subset=['press_release_id'], keep='first')

    stats['serp_results_count'] = len(serp_df)

    if serp_df.empty:
        print(f"[{run_id}] No new URLs after deduplication")
        return stats, None

    # ------------------------------------------------------------------
    # Step 9: Save to CSV (article_scraper.py reads this file)
    # ------------------------------------------------------------------
    print(f"[{run_id}] Saving {len(serp_df):,} new SERP results...")
    serp_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)

    return stats, serp_df


def run_article_scraping(run_id: str, storage: BigQueryStorage) -> Dict[str, Any]:
    """
    Execute article scraping and write results to the new split tables.

    Reads f100_collected_results.csv (written by run_serp_collection),
    runs article_scraper.py as a subprocess, then writes:
        • press_release_metadata  — SERP + company fields (from f100_joined.csv)
        • press_release_content   — scraped text (rows where article_text is set)
        • article_enrichments     — sentiment (from enriched.csv)

    Returns:
        Stats dictionary.
    """
    import subprocess
    import sys

    stats: Dict[str, Any] = {}

    print(f"[{run_id}] Launching article scraper...")

    result = subprocess.run(
        [sys.executable, "article_scraper.py"],
        capture_output=True,
        text=True,
        timeout=3600  # 1 hour
    )

    if result.returncode != 0:
        print(f"[{run_id}] Article scraper failed:\n{result.stderr}")
        raise RuntimeError(f"Article scraper exited with code {result.returncode}")

    # ------------------------------------------------------------------
    # Write metadata + content from joined CSV (has scraped / better titles)
    # ------------------------------------------------------------------
    if config.JOINED_RESULTS_FILE.exists():
        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)
        stats['articles_scraped'] = int(joined_df['article_text'].notna().sum())

        if not joined_df.empty:
            # Metadata (one row per release — includes all SERP + company fields)
            storage.write_press_release_metadata(joined_df, run_id=run_id)

            # Content (only rows where scraping succeeded)
            content_df = joined_df[joined_df['article_text'].notna()].copy()
            if not content_df.empty:
                storage.write_press_release_content(content_df, run_id=run_id)
    else:
        print(f"[{run_id}] ⚠️  {config.JOINED_RESULTS_FILE} not found — no metadata/content written")
        stats['articles_scraped'] = 0

    # ------------------------------------------------------------------
    # Write enrichments from enriched CSV (adds sentiment column)
    # ------------------------------------------------------------------
    if config.ENRICHED_RESULTS_FILE.exists():
        enriched_df = pd.read_csv(config.ENRICHED_RESULTS_FILE)
        stats['articles_enriched'] = len(enriched_df)

        if not enriched_df.empty:
            enrichments_only = enriched_df[['url', 'sentiment']].copy() \
                if 'url' in enriched_df.columns else pd.DataFrame()
            if not enrichments_only.empty:
                storage.write_article_enrichments(
                    enrichments_only, run_id=run_id, enrichment_version="v1.0"
                )
    else:
        stats['articles_enriched'] = 0

    return stats


# ---------------------------------------------------------------------------
# Cloud Run entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def press_release_collection(request: Request):
    """
    Cloud Run HTTP endpoint for press release collection.

    Daily usage (no body needed):
        curl -X POST $SERVICE_URL

    The endpoint will auto-detect the correct start_date from the last
    successful run logged in BigQuery's collection_runs table.

    Manual date range:
        curl -X POST $SERVICE_URL \\
          -H 'Content-Type: application/json' \\
          -d '{"start_date": "2026-03-01", "end_date": "2026-03-04"}'
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
        # Initialise BigQuery early — needed for date auto-detection
        # ------------------------------------------------------------------
        storage = BigQueryStorage()
        storage.initialize_tables()

        # ------------------------------------------------------------------
        # Determine date range
        # Auto-detect start_date from the last successful run when not provided
        # ------------------------------------------------------------------
        today = datetime.utcnow().date()

        if 'start_date' not in request_json:
            last_end_date = storage.get_last_successful_run_end_date()
            if last_end_date:
                # Start from last run's end_date (1-day overlap catches any
                # articles that were published but not yet indexed last time)
                auto_start = last_end_date
                print(f"[{run_id}] Auto start_date: {auto_start} "
                      f"(same as last run's end_date — 1-day overlap for safety)")
            else:
                # First ever run: default 7-day lookback
                auto_start = (today - timedelta(days=7)).strftime('%Y-%m-%d')
                print(f"[{run_id}] No prior completed runs found — "
                      f"defaulting to 7-day lookback: {auto_start}")
            request_json['start_date'] = auto_start

        if 'end_date' not in request_json:
            request_json['end_date'] = today.strftime('%Y-%m-%d')
            print(f"[{run_id}] Auto end_date: {request_json['end_date']} (today)")

        # ------------------------------------------------------------------
        # Validate
        # ------------------------------------------------------------------
        is_valid, error_msg, params = validate_request(request_json)
        if not is_valid:
            response['message'] = error_msg
            return json.dumps(response), 400

        print(f"[{run_id}] Date range: {params['start_date']} → {params['end_date']}")

        # ------------------------------------------------------------------
        # Log run start
        # ------------------------------------------------------------------
        reference_df = grab_reference_data()
        if 'corporation' in reference_df.columns:
            companies = reference_df['corporation'].tolist()
        elif 'Company' in reference_df.columns:
            companies = reference_df['Company'].tolist()
        else:
            companies = []

        storage.log_run_start(
            run_id=run_id,
            start_date=params['start_date'],
            end_date=params['end_date'],
            companies=companies[:100],
        )

        # ------------------------------------------------------------------
        # SERP collection
        # ------------------------------------------------------------------
        serp_stats, serp_df = run_serp_collection(
            start_date=params['start_date'],
            end_date=params['end_date'],
            force_refresh=params['force_refresh'],
            run_id=run_id,
            storage=storage,
        )
        response['stats'].update({k: v for k, v in serp_stats.items() if k != 'all_queries'})

        # ------------------------------------------------------------------
        # Article scraping  /  metadata-only write
        # ------------------------------------------------------------------
        if serp_df is not None and not serp_df.empty:
            if params['skip_scraping']:
                # Write SERP metadata directly (no scraped titles, but still useful)
                storage.write_press_release_metadata(serp_df, run_id=run_id)
                response['stats']['articles_scraped'] = 0
                print(f"[{run_id}] Scraping skipped — wrote {len(serp_df):,} metadata rows only")
            else:
                scraping_stats = run_article_scraping(run_id=run_id, storage=storage)
                response['stats'].update(scraping_stats)
        else:
            response['stats']['articles_scraped'] = 0
            if params['skip_scraping']:
                print(f"[{run_id}] Scraping skipped and no new SERP results")

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
            f"{response['stats'].get('serp_results_count', 0)} new URLs collected, "
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

        # Best-effort failure log
        try:
            if 'storage' in locals():
                storage.log_run_completion(
                    run_id=run_id,
                    urls_collected=response['stats'].get('serp_results_count', 0),
                    articles_scraped=response['stats'].get('articles_scraped', 0),
                    queries_executed=[],
                    error_message=str(e),
                )
        except Exception:
            pass

        return json.dumps(response), 500


# ---------------------------------------------------------------------------
# Local test server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from flask import Flask, request as flask_request

    app = Flask(__name__)

    @app.route('/', methods=['POST'])
    def local_handler():
        return press_release_collection(flask_request)

    print("🚀 Local test server: http://localhost:8080")
    print("No-param daily run:  curl -X POST http://localhost:8080")
    print("Explicit dates:      curl -X POST http://localhost:8080 "
          "-H 'Content-Type: application/json' "
          "-d '{\"start_date\": \"2026-03-01\", \"end_date\": \"2026-03-04\"}'")
    app.run(host='0.0.0.0', port=8080, debug=True)
