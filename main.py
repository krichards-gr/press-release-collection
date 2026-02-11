"""
Press Release Collection Pipeline - Cloud Run HTTP Endpoint
============================================================

Google Cloud Run function for collecting and processing corporate press releases.
Designed to be stateless, scalable, and production-ready.

HTTP API:
    POST /
    Body: {
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",
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
    - BRIGHT_DATA_PROXY_URL: Bright Data proxy credentials
    - BIGQUERY_DATASET: Dataset name (default: press_release_collection)
    - GCP_PROJECT: Google Cloud project ID
"""

import os
import json
import traceback
from datetime import datetime
from typing import Dict, Any
import functions_framework
from flask import Request

# Pipeline modules
from config import config
from grab_reference_data import grab_reference_data
from generate_queries import create_search_queries
from collect_results import collect_search_results
from bigquery_storage import BigQueryStorage


def validate_request(request_json: Dict) -> tuple[bool, str, Dict]:
    """
    Validate incoming request parameters.

    Returns:
        (is_valid, error_message, validated_params)
    """
    from datetime import datetime, timedelta

    # Calculate dynamic dates if not provided
    today = datetime.utcnow().date()
    default_end_date = today.strftime('%Y-%m-%d')

    # Default: collect last 10 days (safety buffer)
    default_start_date = (today - timedelta(days=10)).strftime('%Y-%m-%d')

    # Default parameters
    params = {
        'start_date': request_json.get('start_date', default_start_date),
        'end_date': request_json.get('end_date', default_end_date),
        'force_refresh': request_json.get('force_refresh', False),
        'skip_scraping': request_json.get('skip_scraping', False),
        'use_last_collection': request_json.get('use_last_collection', False),  # Optional smart mode
    }

    # Validate date format
    try:
        datetime.strptime(params['start_date'], '%Y-%m-%d')
        datetime.strptime(params['end_date'], '%Y-%m-%d')
    except ValueError:
        return False, "Invalid date format. Use YYYY-MM-DD", {}

    # Validate date order
    if params['start_date'] > params['end_date']:
        return False, "start_date must be before end_date", {}

    return True, "", params


def run_serp_collection(start_date: str, end_date: str, force_refresh: bool, run_id: str, storage: BigQueryStorage) -> Dict[str, Any]:
    """
    Execute SERP collection stage with deduplication and backfill support.

    Returns:
        Stats dictionary with results
    """
    stats = {}

    # Step 1: Get reference data
    print(f"[{run_id}] Fetching reference data...")
    reference_df = grab_reference_data(force_refresh=force_refresh)

    if reference_df.empty:
        raise ValueError("No reference data available")

    stats['companies_count'] = len(reference_df)

    # Extract company identifiers for logging
    companies = reference_df['Company'].tolist() if 'Company' in reference_df.columns else []

    # Step 2: Check for new URLs needing backfill
    print(f"[{run_id}] Checking for new URLs needing backfill...")

    # Get all URLs from reference data (pressroom URLs are what we track)
    current_urls = []
    if 'pressroom_url' in reference_df.columns:
        current_urls = reference_df['pressroom_url'].dropna().tolist()

    backfill_urls = storage.identify_urls_needing_backfill(
        current_urls=current_urls,
        backfill_start_date="2026-01-01"
    )

    # Step 3: Determine effective date range
    # If there are new URLs, backfill from 2026-01-01
    effective_start_date = start_date
    if backfill_urls and not force_refresh:
        effective_start_date = "2026-01-01"
        print(f"[{run_id}] 🔄 Backfilling {len(backfill_urls)} new URLs from {effective_start_date}")
        stats['backfill_urls_count'] = len(backfill_urls)
    else:
        stats['backfill_urls_count'] = 0

    # Step 4: Generate queries
    print(f"[{run_id}] Generating search queries for {effective_start_date} to {end_date}...")
    # Save reference data to inputs for query generation
    reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)
    all_queries = create_search_queries(start_date=effective_start_date, end_date=end_date)
    stats['queries_generated'] = len(all_queries)

    # Step 5: Query-level deduplication (BEFORE hitting SERP API)
    # This saves SERP API costs by skipping already-executed queries
    queries_to_execute = all_queries

    if not force_refresh:
        print(f"[{run_id}] Checking for already-executed queries (pre-SERP deduplication)...")
        already_executed = storage.get_executed_queries_for_date_range(
            start_date=effective_start_date,
            end_date=end_date
        )

        if already_executed:
            # Filter out queries we've already run
            queries_to_execute = [q for q in all_queries if q not in already_executed]
            skipped_count = len(all_queries) - len(queries_to_execute)
            print(f"[{run_id}] 💰 Skipped {skipped_count:,} already-executed queries (saves SERP API costs)")
            stats['queries_skipped'] = skipped_count
        else:
            stats['queries_skipped'] = 0
    else:
        print(f"[{run_id}] ⚠️  Force refresh enabled - executing all queries")
        stats['queries_skipped'] = 0

    stats['queries_executed'] = len(queries_to_execute)

    # Step 6: Collect SERP results (only for new queries)
    if not queries_to_execute:
        print(f"[{run_id}] ✓ All queries already executed - nothing to collect")
        stats['serp_results_count'] = 0
        # Return queries that were skipped for logging
        stats['all_queries'] = all_queries
        return stats

    print(f"[{run_id}] Collecting SERP results for {len(queries_to_execute):,} queries...")
    serp_df = collect_search_results(search_queries=queries_to_execute)

    # Store executed queries for logging
    stats['all_queries'] = queries_to_execute

    if serp_df is None or serp_df.empty:
        stats['serp_results_count'] = 0
        print(f"[{run_id}] No SERP results collected")
        return stats

    # Step 7: Rename link to url
    serp_df = serp_df.rename(columns={"link": "url"}) if "link" in serp_df.columns else serp_df
    stats['serp_results_count'] = len(serp_df)

    if serp_df.empty:
        print(f"[{run_id}] No new URLs to process after deduplication")
        return stats

    # Step 7: Save SERP results to CSV (for article scraper to read)
    print(f"[{run_id}] Saving {len(serp_df):,} new SERP results...")
    serp_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)

    return stats


def run_article_scraping(run_id: str, storage: BigQueryStorage) -> Dict[str, Any]:
    """
    Execute article scraping stage.

    Returns:
        Stats dictionary with results
    """
    import subprocess
    import sys

    stats = {}

    print(f"[{run_id}] Launching article scraper...")

    # Run article scraper as subprocess
    result = subprocess.run(
        [sys.executable, "article_scraper.py"],
        capture_output=True,
        text=True,
        timeout=3600  # 1 hour timeout
    )

    if result.returncode != 0:
        print(f"[{run_id}] Article scraper failed: {result.stderr}")
        raise RuntimeError(f"Article scraper exited with code {result.returncode}")

    # Load results and write to BigQuery
    import pandas as pd

    # Load joined results (SERP + scraped content)
    if config.JOINED_RESULTS_FILE.exists():
        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)
        stats['articles_scraped'] = len(joined_df[joined_df['article_text'].notna()])

        # Write all collected articles to BigQuery (SERP + content)
        # This goes into the collected_articles table
        if not joined_df.empty:
            storage.write_collected_articles(joined_df, run_id=run_id)

    # Load enriched results (just sentiment for now)
    if config.ENRICHED_RESULTS_FILE.exists():
        enriched_df = pd.read_csv(config.ENRICHED_RESULTS_FILE)
        stats['articles_enriched'] = len(enriched_df)

        # Write enrichments to separate table (url + sentiment)
        # This goes into the article_enrichments table
        if not enriched_df.empty:
            enrichments_only = enriched_df[['url', 'sentiment']].copy()
            # Could add sentiment_score here in future
            storage.write_article_enrichments(enrichments_only, run_id=run_id, enrichment_version="v1.0")

    return stats


@functions_framework.http
def press_release_collection(request: Request):
    """
    Cloud Run HTTP endpoint for press release collection.

    Request:
        POST / with JSON body

    Response:
        JSON with status and results
    """
    # Generate unique run ID
    run_id = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    response = {
        'status': 'error',
        'message': '',
        'run_id': run_id,
        'stats': {},
        'timestamp': datetime.utcnow().isoformat()
    }

    try:
        # Parse request
        request_json = request.get_json(silent=True) or {}

        print(f"[{run_id}] Starting pipeline with params: {request_json}")

        # Validate request
        is_valid, error_msg, params = validate_request(request_json)
        if not is_valid:
            response['message'] = error_msg
            return json.dumps(response), 400

        # Initialize BigQuery storage
        storage = BigQueryStorage()
        storage.initialize_tables()

        # Log run start (get companies for tracking)
        reference_df = grab_reference_data(force_refresh=params['force_refresh'])
        companies = reference_df['Company'].tolist() if 'Company' in reference_df.columns else []

        storage.log_run_start(
            run_id=run_id,
            start_date=params['start_date'],
            end_date=params['end_date'],
            companies=companies[:100]  # Limit to avoid excessive data
        )

        # Run SERP collection
        serp_stats = run_serp_collection(
            start_date=params['start_date'],
            end_date=params['end_date'],
            force_refresh=params['force_refresh'],
            run_id=run_id,
            storage=storage
        )
        response['stats'].update(serp_stats)

        # Run article scraping (unless skipped)
        if not params['skip_scraping'] and serp_stats.get('serp_results_count', 0) > 0:
            scraping_stats = run_article_scraping(run_id=run_id, storage=storage)
            response['stats'].update(scraping_stats)
        else:
            response['stats']['articles_scraped'] = 0

        # Log run completion with executed queries
        storage.log_run_completion(
            run_id=run_id,
            urls_collected=response['stats'].get('serp_results_count', 0),
            articles_scraped=response['stats'].get('articles_scraped', 0),
            queries_executed=response['stats'].get('all_queries', []),
            error_message=None
        )

        # Success!
        response['status'] = 'success'
        response['message'] = f"Pipeline completed successfully. Processed {response['stats'].get('serp_results_count', 0)} articles."

        print(f"[{run_id}] Pipeline completed: {response['stats']}")

        return json.dumps(response), 200

    except Exception as e:
        # Error handling
        error_trace = traceback.format_exc()
        print(f"[{run_id}] Pipeline failed: {error_trace}")

        response['status'] = 'error'
        response['message'] = f"{type(e).__name__}: {str(e)}"
        response['error_trace'] = error_trace

        # Log run failure (if storage was initialized)
        try:
            if 'storage' in locals():
                storage.log_run_completion(
                    run_id=run_id,
                    urls_collected=response['stats'].get('serp_results_count', 0),
                    articles_scraped=response['stats'].get('articles_scraped', 0),
                    queries_executed=response['stats'].get('all_queries', []),
                    error_message=str(e)
                )
        except:
            pass  # Don't fail on logging failure

        return json.dumps(response), 500


# For local testing
if __name__ == "__main__":
    from flask import Flask
    app = Flask(__name__)

    @app.route('/', methods=['POST'])
    def local_handler():
        from flask import request as flask_request
        return press_release_collection(flask_request)

    print("🚀 Starting local test server on http://localhost:8080")
    print("Test with: curl -X POST http://localhost:8080 -H 'Content-Type: application/json' -d '{\"start_date\": \"2026-01-01\", \"end_date\": \"2026-01-07\"}'")
    app.run(host='0.0.0.0', port=8080, debug=True)
