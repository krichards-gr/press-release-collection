"""
Press Release Collection Pipeline - Main Orchestrator
=======================================================

End-to-end pipeline for collecting and processing corporate press releases:
1. Fetch company reference data from BigQuery
2. Generate Google search queries for each newsroom
3. Collect SERP results via Bright Data API
4. Scrape full article content with multi-scraper fallback
5. Apply sentiment analysis and enrich data

Usage:
    python main.py [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--force-refresh]

Examples:
    python main.py
    python main.py --start-date 2026-01-01 --end-date 2026-01-31
    python main.py --force-refresh
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import config
from grab_reference_data import grab_reference_data
from generate_queries import create_search_queries
from collect_results import collect_search_results


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

    return parser.parse_args()


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


def run_pipeline(start_date: str, end_date: str, force_refresh: bool = False, skip_scraping: bool = False):
    """
    Execute the complete press release collection pipeline.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        force_refresh: Force refresh of cached data
        skip_scraping: Skip article scraping step
    """
    print("="*80)
    print("PRESS RELEASE COLLECTION PIPELINE")
    print("="*80)
    print(f"Date Range: {start_date} to {end_date}")
    print(f"Output Directory: {config.OUTPUTS_DIR}")
    print("="*80 + "\n")

    try:
        # =====================================================================
        # STEP 1: Fetch Company Reference Data
        # =====================================================================
        print("📊 STEP 1: Fetching Company Reference Data")
        print("-" * 80)
        reference_df = grab_reference_data(force_refresh=force_refresh)

        if reference_df.empty:
            print("❌ No reference data found. Exiting.")
            sys.exit(1)

        print()

        # =====================================================================
        # STEP 2: Generate Search Queries
        # =====================================================================
        print("🔍 STEP 2: Generating Search Queries")
        print("-" * 80)

        # Save reference data for query generation
        reference_df.to_csv(config.REFERENCE_DATA_FILE, index=False)

        search_queries = create_search_queries(start_date=start_date, end_date=end_date)
        print(f"✓ Generated {len(search_queries):,} search queries")
        print()

        # =====================================================================
        # STEP 3: Collect SERP Results
        # =====================================================================
        print("🌐 STEP 3: Collecting SERP Results")
        print("-" * 80)
        results_df = collect_search_results(search_queries=search_queries)

        if results_df is None or results_df.empty:
            print("❌ No SERP results collected. Exiting.")
            sys.exit(1)

        # Save SERP results
        results_df.to_csv(config.COLLECTED_RESULTS_FILE, index=False)
        print(f"💾 Saved SERP results to: {config.COLLECTED_RESULTS_FILE}")
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

            except Exception as e:
                print(f"❌ Article scraper failed: {e}")
                print("   SERP results are still available in outputs/")
        else:
            print("⏭️  STEP 4: Skipping article scraping (--skip-scraping)")
            print()

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
            print(f"  • Enriched Data:   {config.ENRICHED_RESULTS_FILE}")
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


if __name__ == "__main__":
    args = parse_arguments()

    # Validate dates
    start_date, end_date = validate_dates(args.start_date, args.end_date)

    # Run the pipeline
    run_pipeline(
        start_date=start_date,
        end_date=end_date,
        force_refresh=args.force_refresh,
        skip_scraping=args.skip_scraping
    )
