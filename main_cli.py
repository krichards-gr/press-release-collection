"""
Press Release Collection Pipeline - Main Orchestrator
=======================================================

End-to-end pipeline for collecting and processing corporate press releases:
1. Fetch company reference data from BigQuery
2. Generate Google search queries for each newsroom
3. Collect SERP results via Bright Data API
4. Scrape full article content with multi-scraper fallback

Usage:
    python main.py [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--force-refresh]

Examples:
    python main.py
    python main.py --start-date 2026-01-01 --end-date 2026-01-31
    python main.py --force-refresh
"""

import argparse
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

from config import config
from grab_reference_data import grab_reference_data
from generate_queries import create_search_queries
from collect_results import collect_search_results
from deduplication import URLTracker, deduplicate_serp_results
from checkpointing import CheckpointManager, resume_from_checkpoint


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Corporate Press Release Collection Pipeline"
    )

    parser.add_argument(
        '--start-date',
        type=str,
        default=config.DEFAULT_START_DATE,
        help=f'Start date (YYYY-MM-DD). Default: {config.DEFAULT_START_DATE}'
    )

    parser.add_argument(
        '--end-date',
        type=str,
        default=config.DEFAULT_END_DATE,
        help=f'End date (YYYY-MM-DD). Default: {config.DEFAULT_END_DATE}'
    )

    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Force refresh of cached reference data from BigQuery'
    )

    parser.add_argument(
        '--skip-scraping',
        action='store_true',
        help='Skip article scraping (SERP collection only)'
    )

    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from latest checkpoint (if available)'
    )

    parser.add_argument(
        '--no-checkpoints',
        action='store_true',
        help='Disable checkpointing'
    )

    parser.add_argument(
        '--incremental',
        action='store_true',
        help='Incremental mode: process only articles since last run'
    )

    parser.add_argument(
        '--last-n-days',
        type=int,
        metavar='N',
        help='Process articles from the last N days (shortcut for incremental)'
    )

    parser.add_argument(
        '--limit',
        type=int,
        metavar='N',
        help='Only process the first N companies from the input data (useful for local testing)'
    )

    parser.add_argument(
        '--write-to-bigquery',
        action='store_true',
        help='Write results to BigQuery (metadata + content + run log), same as Cloud Run'
    )

    parser.add_argument(
        '--date-update',
        action='store_true',
        help='Standalone mode: update publish_date in BigQuery from local joined CSV (requires --write-to-bigquery)'
    )

    return parser.parse_args()


def find_last_run_date() -> str:
    """
    Find the end date from the most recent successful pipeline run.

    Checks in order:
        1. BigQuery collection_runs table (authoritative — covers Cloud Run runs)
        2. Local checkpoint metadata (fallback for CLI-only workflows)

    Returns:
        Date string in YYYY-MM-DD format, or None if no previous run found.
    """
    # 1. Try BigQuery first (reflects Cloud Run daily runs as well as local runs)
    try:
        from bigquery_storage import BigQueryStorage
        storage = BigQueryStorage()
        last_date = storage.get_last_successful_run_end_date()
        if last_date:
            print(f"📅 Incremental start from BigQuery last run: {last_date}")
            return last_date
    except Exception:
        pass  # Fall through to checkpoint approach

    # 2. Fall back to local checkpoint metadata
    from checkpointing import find_latest_run
    latest_run = find_latest_run()

    if latest_run:
        checkpoint_dir = config.CHECKPOINT_DIR / latest_run
        metadata_file = checkpoint_dir / "metadata.json"

        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                if 'pipeline_info' in metadata and 'end_date' in metadata['pipeline_info']:
                    last_date = metadata['pipeline_info']['end_date']
                    print(f"📅 Incremental start from checkpoint: {last_date}")
                    return last_date
            except Exception:
                pass

    return None


def validate_dates(start_date: str, end_date: str) -> tuple[str, str]:
    """Validate date format and order."""
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        if start > end:
            raise ValueError("Start date must be before end date")

        if end > datetime.now():
            print(f"⚠️  Warning: End date is in the future")

        return start_date, end_date

    except ValueError as e:
        print(f"❌ Invalid date format: {e}")
        print("   Dates must be in YYYY-MM-DD format")
        sys.exit(1)


def run_pipeline(start_date: str, end_date: str, force_refresh: bool = False,
                  skip_scraping: bool = False, resume: bool = False, use_checkpoints: bool = True,
                  limit: int = None, write_to_bigquery: bool = False):
    """
    Execute the complete press release collection pipeline.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        force_refresh: Force refresh of cached data
        skip_scraping: Skip article scraping step
        resume: Resume from latest checkpoint
        use_checkpoints: Enable checkpointing
        limit: Only process the first N companies (for local testing)
        write_to_bigquery: Write results to BigQuery (metadata + content + run log)
    """
    # Initialize checkpoint manager
    checkpoint_manager = None
    if resume:
        checkpoint_manager = resume_from_checkpoint()
        if checkpoint_manager:
            print("✅ Resuming from checkpoint\n")
    elif use_checkpoints:
        checkpoint_manager = CheckpointManager()
        print(f"💾 Checkpointing enabled (run ID: {checkpoint_manager.run_id})\n")

        # Save pipeline info to metadata
        if checkpoint_manager:
            checkpoint_manager.metadata['pipeline_info'] = {
                'start_date': start_date,
                'end_date': end_date,
                'timestamp': datetime.now().isoformat()
            }
            checkpoint_manager._save_metadata()
    # BigQuery setup (when --write-to-bigquery is used)
    storage = None
    run_id = None
    if write_to_bigquery:
        import hashlib
        import pandas as pd
        from bigquery_storage import BigQueryStorage
        storage = BigQueryStorage()
        storage.initialize_tables()
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    print("="*80)
    print("PRESS RELEASE COLLECTION PIPELINE")
    print("="*80)
    print(f"Date Range: {start_date} to {end_date}")
    print(f"Output Directory: {config.OUTPUTS_DIR}")
    if write_to_bigquery:
        print(f"BigQuery:   ENABLED (run_id: {run_id})")
    print("="*80 + "\n")

    try:
        # =====================================================================
        # STEP 1: Fetch Company Reference Data
        # =====================================================================
        print("📊 STEP 1: Fetching Company Reference Data")
        print("-" * 80)
        reference_df = grab_reference_data()

        # Log run start to BigQuery
        if storage is not None:
            if 'corporation' in reference_df.columns:
                companies = reference_df['corporation'].tolist()
            elif 'Company' in reference_df.columns:
                companies = reference_df['Company'].tolist()
            else:
                companies = []
            storage.log_run_start(
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                companies=companies[:100],
            )

        if reference_df.empty:
            print("❌ No reference data found. Exiting.")
            sys.exit(1)

        print()

        # =====================================================================
        # STEP 2: Generate Search Queries
        # =====================================================================
        print("🔍 STEP 2: Generating Search Queries")
        print("-" * 80)

        # Save full reference data (preserves cache for subsequent runs)
        reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)

        search_queries = create_search_queries(start_date=start_date, end_date=end_date, limit=limit)
        print(f"✓ Generated {len(search_queries):,} search queries")
        print()

        # =====================================================================
        # STEP 3: Collect SERP Results
        # =====================================================================
        if checkpoint_manager and checkpoint_manager.has_checkpoint('serp_results'):
            print("🌐 STEP 3: Loading SERP Results from Checkpoint")
            print("-" * 80)
            results_df = checkpoint_manager.load_checkpoint('serp_results')
            print(f"✓ Loaded {len(results_df):,} SERP results from checkpoint\n")
        else:
            print("🌐 STEP 3: Collecting SERP Results")
            print("-" * 80)
            results_df = collect_search_results(search_queries=search_queries)

            if results_df is None or results_df.empty:
                print("❌ No SERP results collected. Exiting.")
                sys.exit(1)

            if checkpoint_manager:
                checkpoint_manager.save_checkpoint('serp_results', results_df, "Raw SERP results")

        # Deduplicate against previously processed URLs
        tracker = URLTracker()
        if force_refresh:
            print("\n🔄 Skipping Deduplication (--force-refresh)")
            print("-" * 80)
        else:
            print("\n🔄 Deduplicating Results")
            print("-" * 80)
            original_count = len(results_df)
            results_df = deduplicate_serp_results(results_df, tracker)

            if len(results_df) == 0:
                print("⚠️  All URLs have been processed before. No new articles to scrape.")
                print("   Use --force-refresh to reprocess all URLs")
                sys.exit(0)

        # Save SERP results
        results_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)
        print(f"💾 Saved {len(results_df):,} new SERP results to: {config.COLLECTED_RESULTS_FILE}")

        # Write SERP metadata to BigQuery early (survives if scraping crashes)
        if storage is not None:
            serp_df = pd.read_csv(config.COLLECTED_RESULTS_FILE)
            if 'link' in serp_df.columns:
                serp_df = serp_df.rename(columns={'link': 'url'})
            if 'press_release_id' not in serp_df.columns:
                serp_df['press_release_id'] = serp_df['url'].apply(
                    lambda u: hashlib.md5(str(u).strip().encode()).hexdigest()
                )
            n = storage.write_press_release_metadata(serp_df, run_id=run_id)
            print(f"📊 Wrote {n:,} SERP metadata rows to BigQuery")

        print()

        # =====================================================================
        # STEP 4: Scrape Article Content (Optional)
        # =====================================================================
        if not skip_scraping:
            print("📰 STEP 4: Scraping Article Content")
            print("-" * 80)
            print("Launching article scraper...\n")

            # Import and run article scraper
            # We do this dynamically to avoid loading heavy dependencies unless needed
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, "article_scraper.py"],
                    cwd=config.BASE_DIR,
                    capture_output=False,
                    text=True
                )

                if result.returncode != 0:
                    print(f"⚠️  Article scraper exited with code {result.returncode}")
                else:
                    print()
                    # Mark successfully scraped URLs as processed
                    tracker.mark_batch_as_processed(results_df['link'].tolist())
                    tracker.save_processed_urls()

                    # Write scraped data to BigQuery (metadata with scraped titles + content)
                    if storage is not None and config.JOINED_RESULTS_FILE.exists():
                        joined_df = pd.read_csv(config.JOINED_RESULTS_FILE)
                        if 'link' in joined_df.columns:
                            joined_df = joined_df.rename(columns={'link': 'url'})
                        if 'press_release_id' not in joined_df.columns:
                            joined_df['press_release_id'] = joined_df['url'].apply(
                                lambda u: hashlib.md5(str(u).strip().encode()).hexdigest()
                            )
                        if not joined_df.empty:
                            storage.write_press_release_metadata(joined_df, run_id=run_id)
                            storage.update_metadata_publish_dates(joined_df)
                            content_df = joined_df[joined_df['article_text'].notna()].copy()
                            if not content_df.empty:
                                n = storage.write_press_release_content(content_df, run_id=run_id)
                                print(f"📊 Wrote {n:,} article content rows to BigQuery")

            except Exception as e:
                print(f"❌ Article scraper failed: {e}")
                print("   SERP results are still available in outputs/")
        else:
            print("⏭️  STEP 4: Skipping article scraping (--skip-scraping)")
            print()

        # =====================================================================
        # Log run to BigQuery
        # =====================================================================
        if storage is not None:
            serp_count = len(results_df) if results_df is not None else 0
            scraped_count = 0
            if not skip_scraping and config.JOINED_RESULTS_FILE.exists():
                try:
                    jdf = pd.read_csv(config.JOINED_RESULTS_FILE)
                    scraped_count = int(jdf['article_text'].notna().sum()) if 'article_text' in jdf.columns else 0
                except Exception:
                    pass
            storage.log_run_completion(
                run_id=run_id,
                urls_collected=serp_count,
                articles_scraped=scraped_count,
                queries_executed=search_queries,
                error_message=None,
            )
            print(f"📊 Run {run_id} logged to BigQuery (collected={serp_count}, scraped={scraped_count})")

        # =====================================================================
        # PIPELINE COMPLETE
        # =====================================================================
        print("="*80)
        print("✅ PIPELINE COMPLETE")
        print("="*80)
        print(f"\nOutput files:")
        print(f"  • SERP Results:    {config.COLLECTED_RESULTS_FILE}")
        if not skip_scraping:
            print(f"  • Joined Data:     {config.JOINED_RESULTS_FILE}")
        print()

    except KeyboardInterrupt:
        print("\n\n⚠️  Pipeline interrupted by user")
        sys.exit(1)

    except Exception as e:
        print(f"\n\n❌ Pipeline failed with error:")
        print(f"   {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_date_update():
    """
    Standalone mode: update publish_date on BigQuery metadata rows
    using scraped dates from the local joined CSV.
    """
    import hashlib
    import pandas as pd
    from bigquery_storage import BigQueryStorage

    if not config.JOINED_RESULTS_FILE.exists():
        print(f"❌ {config.JOINED_RESULTS_FILE} not found — run the pipeline first to generate it")
        sys.exit(1)

    print("📅 Loading joined CSV for publish_date update...")
    df = pd.read_csv(config.JOINED_RESULTS_FILE)

    if 'link' in df.columns:
        df = df.rename(columns={'link': 'url'})
    if 'press_release_id' not in df.columns:
        df['press_release_id'] = df['url'].apply(
            lambda u: hashlib.md5(str(u).strip().encode()).hexdigest()
        )

    has_date = df[df['publish_date'].notna()]
    print(f"   {len(has_date):,} of {len(df):,} rows have a scraped publish_date")

    if has_date.empty:
        print("⚠️  No publish dates to update")
        sys.exit(0)

    storage = BigQueryStorage()
    updated = storage.update_metadata_publish_dates(has_date)
    print(f"✅ Done — {updated:,} BigQuery rows updated")


if __name__ == "__main__":
    args = parse_arguments()

    # Standalone date update mode
    if args.date_update:
        if not args.write_to_bigquery:
            print("❌ --date-update requires --write-to-bigquery")
            sys.exit(1)
        run_date_update()
        sys.exit(0)

    # Handle incremental modes
    start_date = args.start_date
    end_date = args.end_date

    if args.last_n_days:
        # Process last N days
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=args.last_n_days)).strftime('%Y-%m-%d')
        print(f"🔄 Incremental mode: Last {args.last_n_days} days ({start_date} to {end_date})")

    elif args.incremental:
        # Try to find last run date from checkpoints or processed URLs
        last_run_date = find_last_run_date()
        if last_run_date:
            start_date = last_run_date
            end_date = datetime.now().strftime('%Y-%m-%d')
            print(f"🔄 Incremental mode: From last run ({start_date} to {end_date})")
        else:
            print("⚠️  No previous run found, using default date range")

    # Validate dates
    start_date, end_date = validate_dates(start_date, end_date)

    # Run the pipeline
    run_pipeline(
        start_date=start_date,
        end_date=end_date,
        force_refresh=args.force_refresh,
        skip_scraping=args.skip_scraping,
        resume=args.resume,
        use_checkpoints=not args.no_checkpoints,
        limit=args.limit,
        write_to_bigquery=args.write_to_bigquery,
    )
