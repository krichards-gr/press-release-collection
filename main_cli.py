"""
Press Release Collection Pipeline -- Local CLI Entry Point
============================================================

This is the LOCAL command-line interface for running the press release
collection pipeline. It orchestrates all pipeline stages in sequence:

  1. Fetch company reference data from BigQuery
     (corporation, sector, newsroom_url for Fortune 100 companies)
  2. Generate Google SERP queries for each newsroom URL + date range
  3. Collect SERP results via Bright Data proxy (concurrent)
  4. Scrape full article content using the 5-scraper fallback chain (concurrent)

For CLOUD RUN, see main.py instead -- it's a stateless HTTP endpoint
that auto-detects dates from BigQuery.

Usage Examples:
  python main_cli.py                                      # Use default date range
  python main_cli.py --start-date 2026-01-01 --end-date 2026-01-31
  python main_cli.py --incremental                        # Auto-detect from last run
  python main_cli.py --last-n-days 7                      # Last 7 days
  python main_cli.py --skip-scraping                      # SERP collection only
  python main_cli.py --write-to-bigquery                  # Write to BQ (like Cloud Run)
  python main_cli.py --limit 5                            # Test with 5 companies only
"""

import argparse
import hashlib
import sys
import subprocess
import traceback
from datetime import datetime, timedelta
from pathlib import Path
import re
import urllib.parse

import pandas as pd

from config import config
from grab_reference_data import grab_reference_data
from generate_queries import create_search_queries
from collect_results import collect_search_results


# =============================================================================
# CLI ARGUMENT PARSING
# =============================================================================

def parse_arguments():
    """
    Parse command-line arguments.

    Returns an argparse.Namespace with all the pipeline options.
    """
    parser = argparse.ArgumentParser(
        description="Corporate Press Release Collection Pipeline (Local CLI)"
    )

    # -- Date range options --
    parser.add_argument(
        '--start-date', type=str, default=config.DEFAULT_START_DATE,
        help=f'Start date (YYYY-MM-DD). Default: {config.DEFAULT_START_DATE}'
    )
    parser.add_argument(
        '--end-date', type=str, default=config.DEFAULT_END_DATE,
        help=f'End date (YYYY-MM-DD). Default: {config.DEFAULT_END_DATE}'
    )
    parser.add_argument(
        '--incremental', action='store_true',
        help='Auto-detect start date from last successful BigQuery run'
    )
    parser.add_argument(
        '--last-n-days', type=int, metavar='N',
        help='Collect articles from the last N days'
    )

    # -- Pipeline control --
    parser.add_argument(
        '--force-refresh', action='store_true',
        help='Bypass all deduplication (re-collect everything)'
    )
    parser.add_argument(
        '--skip-scraping', action='store_true',
        help='Skip article scraping (SERP collection only)'
    )
    parser.add_argument(
        '--limit', type=int, metavar='N',
        help='Only process the first N companies (useful for testing)'
    )

    # -- BigQuery options --
    parser.add_argument(
        '--write-to-bigquery', action='store_true',
        help='Write results to BigQuery (metadata + content + run log)'
    )
    parser.add_argument(
        '--date-update', action='store_true',
        help='Standalone mode: update publish_date in BQ from local joined CSV'
    )
    parser.add_argument(
        '--backfill-dates', action='store_true',
        help='Backfill NULL publish_date rows in BQ using URL/text date extraction'
    )
    parser.add_argument(
        '--no-fallback-date', action='store_true',
        help='With --backfill-dates: skip collection_date fallback, leave unresolved rows NULL'
    )

    return parser.parse_args()


def _extract_newsroom_from_query(query: str) -> str:
    """
    Extract the newsroom base URL from a SERP query string.

    Handles two formats:
      1. Full Google URL: https://www.google.com/search?q=site:https://...
      2. Raw query text:  site:https://about.att.com/story/ before:2026-03-17
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


# =============================================================================
# DATE HELPERS
# =============================================================================

def find_last_run_date() -> str:
    """
    Find the end date from the most recent successful pipeline run.

    Queries the BigQuery collection_runs table for the last completed run
    and returns its end_date. This is used by --incremental mode to
    automatically pick up where the last run left off.

    Returns:
        Date string in YYYY-MM-DD format, or None if no previous run found.
    """
    try:
        from bigquery_storage import BigQueryStorage
        storage = BigQueryStorage()
        last_date = storage.get_last_successful_run_end_date()
        if last_date:
            print(f"Incremental start from BigQuery last run: {last_date}")
            return last_date
    except Exception:
        pass  # BigQuery not available -- no previous run info

    return None


def validate_dates(start_date: str, end_date: str) -> tuple[str, str]:
    """
    Validate that dates are in YYYY-MM-DD format and start <= end.

    Exits with an error message if validation fails.
    """
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        if start > end:
            raise ValueError("Start date must be before end date")

        if end > datetime.now():
            print(f"Warning: End date is in the future")

        return start_date, end_date

    except ValueError as e:
        print(f"Invalid date format: {e}")
        print("   Dates must be in YYYY-MM-DD format")
        sys.exit(1)


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_pipeline(start_date: str, end_date: str, force_refresh: bool = False,
                 skip_scraping: bool = False, limit: int = None,
                 write_to_bigquery: bool = False):
    """
    Execute the complete press release collection pipeline.

    This function runs all 4 stages in sequence:
      1. Fetch reference data (company names, sectors, newsroom URLs)
      2. Generate SERP queries for each newsroom
      3. Collect SERP results via Bright Data
      4. Scrape article content (optional, can be skipped)

    Args:
        start_date:       Start of date range to collect (YYYY-MM-DD).
        end_date:         End of date range to collect (YYYY-MM-DD).
        force_refresh:    If True, skip all deduplication.
        skip_scraping:    If True, only do SERP collection (no article scraping).
        limit:            If set, only process first N companies (for testing).
        write_to_bigquery: If True, write results to BigQuery tables.
    """
    # -- BigQuery setup (optional) --
    # When --write-to-bigquery is used, we initialize BQ storage and create
    # a run_id to track this execution in the collection_runs table.
    storage = None
    run_id = None
    if write_to_bigquery:
        from bigquery_storage import BigQueryStorage
        storage = BigQueryStorage()
        storage.initialize_tables()
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    # -- Print pipeline banner --
    print("=" * 80)
    print("PRESS RELEASE COLLECTION PIPELINE")
    print("=" * 80)
    print(f"Date Range: {start_date} to {end_date}")
    print(f"Output Directory: {config.OUTPUTS_DIR}")
    if write_to_bigquery:
        print(f"BigQuery:   ENABLED (run_id: {run_id})")
    print("=" * 80 + "\n")

    try:
        # =================================================================
        # STEP 1: Fetch Company Reference Data
        # =================================================================
        # Downloads Fortune 100 company data from BigQuery's
        # benchmarking_corporate_reference table. Returns a DataFrame with:
        #   corporation, sector, newsroom_url
        print("STEP 1: Fetching Company Reference Data")
        print("-" * 80)
        reference_df = grab_reference_data()

        # Log run start to BigQuery (if enabled)
        if storage is not None:
            companies = (
                reference_df['corporation'].tolist()
                if 'corporation' in reference_df.columns
                else []
            )
            storage.log_run_start(
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                companies=companies[:100],
            )

        if reference_df.empty:
            print("No reference data found. Exiting.")
            sys.exit(1)

        print()

        # =================================================================
        # STEP 2: Generate Search Queries
        # =================================================================
        # Builds one Google SERP query per company newsroom URL, scoped
        # to the date range. Format:
        #   site:{newsroom_url}+before:{end}+after:{start}
        print("STEP 2: Generating Search Queries")
        print("-" * 80)

        # Save reference data to CSV (generate_queries.py reads this file)
        reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)

        search_queries = create_search_queries(
            start_date=start_date, end_date=end_date, limit=limit
        )
        print(f"Generated {len(search_queries):,} search queries\n")

        # =================================================================
        # STEP 3: Collect SERP Results
        # =================================================================
        # Sends all queries to Google via the Bright Data SERP proxy.
        # Runs concurrently with SERP_MAX_WORKERS threads (default 5).
        # Handles pagination, retries, and per-query timeouts.
        print("STEP 3: Collecting SERP Results")
        print("-" * 80)
        results_df = collect_search_results(search_queries=search_queries)

        if results_df is None or results_df.empty:
            print("No SERP results collected. Exiting.")
            sys.exit(1)

        # -- Annotate with newsroom, company, and sector --
        print("Annotating SERP results with corporate reference data...")
        newsroom_to_company = {}
        newsroom_to_sector = {}
        if 'newsroom_url' in reference_df.columns:
            for _, row in reference_df.iterrows():
                newsroom = str(row.get('newsroom_url') or '').strip().rstrip('/')
                corp = str(row.get('corporation') or '').strip()
                sector = str(row.get('sector') or '').strip()
                if newsroom:
                    newsroom_to_company[newsroom] = corp
                    newsroom_to_sector[newsroom] = sector

        # Normalize column name
        if 'link' in results_df.columns:
            results_df = results_df.rename(columns={'link': 'url'})

        # Generate deterministic IDs
        if 'press_release_id' not in results_df.columns:
            results_df['press_release_id'] = results_df['url'].apply(
                lambda u: hashlib.md5(str(u).strip().encode()).hexdigest()
            )

        results_df['newsroom_url'] = results_df['query'].apply(_extract_newsroom_from_query)
        results_df['company'] = results_df['newsroom_url'].map(newsroom_to_company).fillna('')
        results_df['sector'] = results_df['newsroom_url'].map(newsroom_to_sector).fillna('')

        # Save raw SERP results to CSV (article_scraper.py reads this file)
        results_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)
        print(f"Saved {len(results_df):,} SERP results to: "
              f"{config.COLLECTED_RESULTS_FILE}")

        # -- Write SERP metadata to BigQuery early --
        # This ensures metadata survives even if scraping crashes later.
        if storage is not None:
            serp_df = pd.read_csv(config.COLLECTED_RESULTS_FILE)

            n = storage.write_press_release_metadata(serp_df, run_id=run_id)
            print(f"Wrote {n:,} SERP metadata rows to BigQuery")

        print()

        # =================================================================
        # STEP 4: Scrape Article Content (Optional)
        # =================================================================
        # Runs article_scraper.py as a subprocess. It reads the SERP results
        # CSV, scrapes each URL using the 5-scraper fallback chain, and
        # writes the joined output CSV.
        if not skip_scraping:
            print("STEP 4: Scraping Article Content")
            print("-" * 80)
            print("Launching article scraper...\n")

            try:
                result = subprocess.run(
                    [sys.executable, "article_scraper.py"],
                    cwd=config.BASE_DIR,
                    capture_output=False,
                    text=True
                )

                if result.returncode != 0:
                    print(f"Warning: Article scraper exited with code "
                          f"{result.returncode}")
                else:
                    print()

                    # Write scraped content to BigQuery (if enabled)
                    if (storage is not None
                            and config.JOINED_RESULTS_FILE.exists()):
                        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)

                        if 'link' in joined_df.columns:
                            joined_df = joined_df.rename(columns={'link': 'url'})
                        if 'press_release_id' not in joined_df.columns:
                            joined_df['press_release_id'] = joined_df['url'].apply(
                                lambda u: hashlib.md5(
                                    str(u).strip().encode()
                                ).hexdigest()
                            )

                        if not joined_df.empty:
                            # Update metadata with scraped titles
                            storage.write_press_release_metadata(
                                joined_df, run_id=run_id
                            )
                            # Backfill publish_date from scraped data
                            storage.update_metadata_publish_dates(joined_df)
                            # Write article content
                            content_df = joined_df[
                                joined_df['article_text'].notna()
                            ].copy()
                            if not content_df.empty:
                                n = storage.write_press_release_content(
                                    content_df, run_id=run_id
                                )
                                print(f"Wrote {n:,} article content rows to "
                                      f"BigQuery")

            except Exception as e:
                print(f"Article scraper failed: {e}")
                print("   SERP results are still available in outputs/")
        else:
            print("STEP 4: Skipping article scraping (--skip-scraping)\n")

        # =================================================================
        # Log run completion to BigQuery
        # =================================================================
        if storage is not None:
            serp_count = len(results_df) if results_df is not None else 0
            scraped_count = 0
            if not skip_scraping and config.JOINED_RESULTS_FILE.exists():
                try:
                    jdf = pd.read_csv(config.JOINED_RESULTS_FILE)
                    scraped_count = int(
                        jdf['article_text'].notna().sum()
                    ) if 'article_text' in jdf.columns else 0
                except Exception:
                    pass
            storage.log_run_completion(
                run_id=run_id,
                urls_collected=serp_count,
                articles_scraped=scraped_count,
                queries_executed=search_queries,
                error_message=None,
            )
            print(f"Run {run_id} logged to BigQuery "
                  f"(collected={serp_count}, scraped={scraped_count})")

        # =================================================================
        # PIPELINE COMPLETE
        # =================================================================
        print("=" * 80)
        print("PIPELINE COMPLETE")
        print("=" * 80)
        print(f"\nOutput files:")
        print(f"  SERP Results:    {config.COLLECTED_RESULTS_FILE}")
        if not skip_scraping:
            print(f"  Joined Data:     {config.JOINED_RESULTS_FILE}")
        print()

    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user")
        sys.exit(1)

    except Exception as e:
        print(f"\n\nPipeline failed with error:")
        print(f"   {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)


# =============================================================================
# STANDALONE DATE UPDATE MODE
# =============================================================================

def run_date_update():
    """
    Standalone mode: update publish_date on BigQuery metadata rows using
    scraped dates from the local joined CSV.

    This is useful when you've already run the pipeline and scraped articles
    locally, but want to push the extracted publish dates to BigQuery without
    re-running the entire pipeline.

    Requires: --write-to-bigquery flag and outputs/f100_joined.csv to exist.
    """
    from bigquery_storage import BigQueryStorage

    if not config.JOINED_RESULTS_FILE.exists():
        print(f"{config.JOINED_RESULTS_FILE} not found -- run the pipeline first")
        sys.exit(1)

    print("Loading joined CSV for publish_date update...")
    df = pd.read_csv(config.JOINED_RESULTS_FILE)

    # Normalize column names
    if 'link' in df.columns:
        df = df.rename(columns={'link': 'url'})
    if 'press_release_id' not in df.columns:
        df['press_release_id'] = df['url'].apply(
            lambda u: hashlib.md5(str(u).strip().encode()).hexdigest()
        )

    has_date = df[df['publish_date'].notna()]
    print(f"   {len(has_date):,} of {len(df):,} rows have a scraped publish_date")

    if has_date.empty:
        print("No publish dates to update")
        sys.exit(0)

    storage = BigQueryStorage()
    updated = storage.update_metadata_publish_dates(has_date)
    print(f"Done -- {updated:,} BigQuery rows updated")


# =============================================================================
# BACKFILL DATES FROM BIGQUERY
# =============================================================================

def run_backfill_dates(no_fallback_date: bool = False):
    """
    Backfill NULL publish_date rows in BigQuery using URL/text date extraction.

    Fetches all metadata rows where publish_date IS NULL (joined with content
    for article_text), runs the date extraction priority chain
    (URL → text → collection_timestamp), and updates BigQuery.

    Args:
        no_fallback_date: If True, skip the collection_date fallback and leave
                          unresolved rows as NULL.
    """
    from bigquery_storage import BigQueryStorage
    from date_extractor import resolve_dates_df

    storage = BigQueryStorage()

    # Fetch rows needing dates
    df = storage.fetch_null_publish_date_rows()
    if df.empty:
        print("No rows with NULL publish_date — nothing to backfill")
        return

    # Run date extraction chain (scraper date is already NULL for these rows,
    # so it will fall through to URL → text → collection_timestamp)
    df['publish_date'] = None  # ensure column exists for resolve_dates_df
    df = resolve_dates_df(df)

    if no_fallback_date:
        # Clear dates that came from the collection_date fallback
        df.loc[df['publish_date_source'] == 'collection_date', 'publish_date'] = None
    else:
        # Use collection_timestamp as the fallback instead of today's date,
        # since these are historical rows
        for i, row in df.iterrows():
            if row.get('publish_date_source') == 'collection_date':
                ct = row.get('collection_timestamp')
                if ct is not None:
                    df.at[i, 'publish_date'] = ct.date() if hasattr(ct, 'date') else ct

    # Report source breakdown
    source_counts = df['publish_date_source'].value_counts()
    print(f"\nDate sources for {len(df):,} rows:")
    for source, count in source_counts.items():
        label = f"{source} (skipped)" if source == 'collection_date' and no_fallback_date else source
        print(f"   {label:.<30} {count:>5} ({count / len(df) * 100:.1f}%)")

    # Update BigQuery (only rows where we found a date)
    has_date = df[df['publish_date'].notna()].copy()
    if has_date.empty:
        print("No dates resolved — nothing to update")
        return

    print(f"\nUpdating {len(has_date):,} rows in BigQuery...")
    updated = storage.update_metadata_publish_dates(has_date)
    print(f"Done — {updated:,} BigQuery rows updated")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    args = parse_arguments()

    # -- Backfill NULL publish_date rows in BQ --
    if args.backfill_dates:
        run_backfill_dates(no_fallback_date=args.no_fallback_date)
        sys.exit(0)

    # -- Standalone date update mode --
    if args.date_update:
        if not args.write_to_bigquery:
            print("--date-update requires --write-to-bigquery")
            sys.exit(1)
        run_date_update()
        sys.exit(0)

    # -- Determine date range --
    start_date = args.start_date
    end_date = args.end_date

    if args.last_n_days:
        # --last-n-days N: collect the most recent N days
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (
            datetime.now() - timedelta(days=args.last_n_days)
        ).strftime('%Y-%m-%d')
        print(f"Incremental mode: Last {args.last_n_days} days "
              f"({start_date} to {end_date})")

    elif args.incremental:
        # --incremental: auto-detect from BigQuery's collection_runs table
        last_run_date = find_last_run_date()
        if last_run_date:
            start_date = last_run_date
            end_date = datetime.now().strftime('%Y-%m-%d')
            print(f"Incremental mode: From last run "
                  f"({start_date} to {end_date})")
        else:
            print("No previous run found, using default date range")

    # -- Validate dates and run the pipeline --
    start_date, end_date = validate_dates(start_date, end_date)

    run_pipeline(
        start_date=start_date,
        end_date=end_date,
        force_refresh=args.force_refresh,
        skip_scraping=args.skip_scraping,
        limit=args.limit,
        write_to_bigquery=args.write_to_bigquery,
    )
