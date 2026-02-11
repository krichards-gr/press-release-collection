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
    # Default parameters
    params = {
        'start_date': request_json.get('start_date', config.DEFAULT_START_DATE),
        'end_date': request_json.get('end_date', config.DEFAULT_END_DATE),
        'force_refresh': request_json.get('force_refresh', False),
        'skip_scraping': request_json.get('skip_scraping', False),
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
    Execute SERP collection stage.

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

    # Step 2: Generate queries
    print(f"[{run_id}] Generating search queries...")
    # Save reference data to inputs for query generation
    reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)
    search_queries = create_search_queries(start_date=start_date, end_date=end_date)
    stats['queries_count'] = len(search_queries)

    # Step 3: Collect SERP results
    print(f"[{run_id}] Collecting SERP results...")
    serp_df = collect_search_results(search_queries=search_queries)

    if serp_df is None or serp_df.empty:
        stats['serp_results_count'] = 0
        print(f"[{run_id}] No SERP results collected")
        return stats

    stats['serp_results_count'] = len(serp_df)

    # Step 4: Write to BigQuery
    print(f"[{run_id}] Writing SERP results to BigQuery...")
    serp_df = serp_df.rename(columns={"link": "url"}) if "link" in serp_df.columns else serp_df
    storage.write_serp_results(serp_df, run_id=run_id)

    # Optional: Also save to CSV for backup/debugging
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

    # Load joined results
    if config.JOINED_RESULTS_FILE.exists():
        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)
        stats['articles_scraped'] = len(joined_df[joined_df['article_text'].notna()])

        # Write scraped articles to BigQuery
        scraped_df = joined_df[joined_df['article_text'].notna()].copy()
        if not scraped_df.empty:
            storage.write_scraped_articles(scraped_df, run_id=run_id)

    # Load enriched results
    if config.ENRICHED_RESULTS_FILE.exists():
        enriched_df = pd.read_csv(config.ENRICHED_RESULTS_FILE)
        stats['articles_enriched'] = len(enriched_df)

        # Write enriched articles to BigQuery
        storage.write_enriched_articles(enriched_df, run_id=run_id)

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
