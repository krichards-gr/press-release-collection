"""
BigQuery Storage Module
========================

Manages BigQuery table operations for the press release collection pipeline.

Schema Design:
--------------
1. press_release_metadata: One row per press release — SERP fields + company info (immutable)
2. press_release_content:  One row per scraped release — article text, summary, keywords
3. collection_runs:        Pipeline execution log — idempotency, backfill, run auditing

The metadata/content split mirrors the earnings-call-collector pattern:
  earnings_call_transcript_metadata  ↔  press_release_metadata
  earnings_call_transcript_content   ↔  press_release_content

press_release_id is a deterministic MD5(url) — the same URL always produces the same ID,
enabling BigQuery-first deduplication before any SERP API calls are made.

Legacy:
-------
collected_articles: Original combined table (pre-split). Still exists in BigQuery with
historical data. No longer written to by this pipeline; use press_release_metadata
+ press_release_content for all new writes and reads.
"""

import hashlib
import os
from datetime import datetime
from typing import Optional, List, Dict
import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from config import config


def _generate_press_release_id(url: str) -> str:
    """Generate a deterministic press_release_id from a URL using MD5.

    Analogous to the MD5(symbol + report_date) ID used in earnings-call-collector.
    Same URL always produces the same ID, enabling idempotent inserts.
    """
    return hashlib.md5(str(url).encode('utf-8')).hexdigest()


class BigQueryStorage:
    """Handle all BigQuery operations for the pipeline."""

    def __init__(self, project_id: str = None, dataset_id: str = None):
        """
        Initialize BigQuery storage.

        Args:
            project_id: GCP project ID (default: from environment)
            dataset_id: BigQuery dataset ID (default: pressure_monitoring)
        """
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id or self.client.project
        self.dataset_id = dataset_id or config.BIGQUERY_DATASET
        self.dataset_ref = f"{self.project_id}.{self.dataset_id}"

        # Ensure dataset exists
        self._ensure_dataset_exists()

    def _ensure_dataset_exists(self):
        """Create dataset if it doesn't exist."""
        try:
            self.client.get_dataset(self.dataset_ref)
            print(f"✓ Using BigQuery dataset: {self.dataset_ref}")
        except NotFound:
            dataset = bigquery.Dataset(self.dataset_ref)
            dataset.location = "US"
            dataset = self.client.create_dataset(dataset)
            print(f"✓ Created BigQuery dataset: {self.dataset_ref}")

    def _get_table_ref(self, table_name: str) -> str:
        """Get fully qualified table reference."""
        return f"{self.dataset_ref}.{table_name}"

    # =========================================================================
    # TABLE CREATION
    # =========================================================================

    def create_press_release_metadata_table(self):
        """
        Create table for press release metadata (SERP fields + company info).

        One row per press release. Immutable once collected.
        Analogous to earnings_call_transcript_metadata in earnings-call-collector.

        Clustered by company for efficient per-company queries.
        """
        table_id = self._get_table_ref("press_release_metadata")

        schema = [
            # Deterministic ID (joins to press_release_content)
            bigquery.SchemaField("press_release_id", "STRING", mode="REQUIRED",
                                 description="MD5(url) — deterministic ID linking to content table"),
            bigquery.SchemaField("url", "STRING", mode="REQUIRED",
                                 description="Article URL"),

            # SERP fields
            bigquery.SchemaField("title", "STRING", mode="NULLABLE",
                                 description="Article title (scraped page title when available, otherwise SERP title)"),
            bigquery.SchemaField("description", "STRING", mode="NULLABLE",
                                 description="Meta description from SERP"),
            bigquery.SchemaField("rank", "INTEGER", mode="NULLABLE",
                                 description="Search result rank position"),
            bigquery.SchemaField("query", "STRING", mode="NULLABLE",
                                 description="Full Google search query URL that found this article"),

            # Company context
            bigquery.SchemaField("company", "STRING", mode="NULLABLE",
                                 description="Corporation name from reference data (e.g. Apple Inc.)"),
            bigquery.SchemaField("newsroom_url", "STRING", mode="NULLABLE",
                                 description="Newsroom base URL used in site: search (e.g. newsroom.apple.com)"),

            # Dates
            bigquery.SchemaField("publish_date", "TIMESTAMP", mode="NULLABLE",
                                 description="Article publication date (scraped where available)"),
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP", mode="REQUIRED",
                                 description="When article was collected by this pipeline"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE",
                                 description="Pipeline run identifier (YYYYMMDD_HHMMSS)"),
        ]

        table = bigquery.Table(table_id, schema=schema)

        # Partition by collection date for efficient time-range queries
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )

        # Cluster by company first (most common filter), then press_release_id
        table.clustering_fields = ["company", "press_release_id"]

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_press_release_content_table(self):
        """
        Create table for press release content (scraped article text).

        One row per successfully scraped press release.
        Analogous to earnings_call_transcript_content in earnings-call-collector.

        Join to press_release_metadata on press_release_id.
        """
        table_id = self._get_table_ref("press_release_content")

        schema = [
            # Foreign key to metadata table
            bigquery.SchemaField("press_release_id", "STRING", mode="REQUIRED",
                                 description="MD5(url) — foreign key to press_release_metadata"),

            # Scraped content
            bigquery.SchemaField("article_text", "STRING", mode="NULLABLE",
                                 description="Full article text extracted by scraper"),
            bigquery.SchemaField("summary", "STRING", mode="NULLABLE",
                                 description="Auto-generated summary (newspaper3k NLP)"),
            bigquery.SchemaField("keywords", "STRING", mode="NULLABLE",
                                 description="Extracted keywords (comma-separated, newspaper3k NLP)"),
            bigquery.SchemaField("scraper_used", "STRING", mode="NULLABLE",
                                 description="Which scraper successfully extracted content"),

            # Metadata
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP", mode="REQUIRED",
                                 description="When content was scraped"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE",
                                 description="Pipeline run identifier"),
        ]

        table = bigquery.Table(table_id, schema=schema)

        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )

        table.clustering_fields = ["press_release_id"]

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_collection_runs_table(self):
        """
        Create table for tracking pipeline execution runs.

        This table enables idempotency by tracking what has been collected
        and supports both query-level and URL-level deduplication.

        Key use: get_last_successful_run_end_date() reads this table to
        determine where the next daily run should start.
        """
        table_id = self._get_table_ref("collection_runs")

        schema = [
            bigquery.SchemaField("run_id", "STRING", mode="REQUIRED",
                                 description="Unique run identifier (YYYYMMDD_HHMMSS)"),
            bigquery.SchemaField("start_date", "DATE", mode="REQUIRED",
                                 description="Start of date range collected"),
            bigquery.SchemaField("end_date", "DATE", mode="REQUIRED",
                                 description="End of date range collected"),
            bigquery.SchemaField("companies_processed", "STRING", mode="REPEATED",
                                 description="List of company identifiers processed"),
            bigquery.SchemaField("queries_executed", "STRING", mode="REPEATED",
                                 description="List of search queries executed (for SERP API deduplication)"),
            bigquery.SchemaField("queries_count", "INTEGER", mode="NULLABLE",
                                 description="Number of queries executed"),
            bigquery.SchemaField("urls_collected", "INTEGER", mode="NULLABLE",
                                 description="Number of URLs collected"),
            bigquery.SchemaField("articles_scraped", "INTEGER", mode="NULLABLE",
                                 description="Number of articles successfully scraped"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED",
                                 description="Run status: started, completed, failed"),
            bigquery.SchemaField("start_timestamp", "TIMESTAMP", mode="REQUIRED",
                                 description="When run started"),
            bigquery.SchemaField("end_timestamp", "TIMESTAMP", mode="NULLABLE",
                                 description="When run completed/failed"),
            bigquery.SchemaField("error_message", "STRING", mode="NULLABLE",
                                 description="Error message if failed"),
        ]

        table = bigquery.Table(table_id, schema=schema)

        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="start_timestamp"
        )

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_collected_articles_table(self):
        """
        [LEGACY] Create the original combined collected_articles table.

        This table is no longer written to by the pipeline. New data goes to
        press_release_metadata + press_release_content. Historical data in this
        table is preserved and can still be queried.
        """
        table_id = self._get_table_ref("collected_articles")

        schema = [
            bigquery.SchemaField("url", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("description", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("rank", "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("query", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("article_text", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("summary", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("keywords", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("publish_date", "TIMESTAMP", mode="NULLABLE"),
            bigquery.SchemaField("scraper_used", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE"),
        ]

        table = bigquery.Table(table_id, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )
        table.clustering_fields = ["url"]

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def initialize_tables(self):
        """Create all required tables if they don't already exist."""
        print("\n📊 Initializing BigQuery tables...")
        self.create_press_release_metadata_table()
        self.create_press_release_content_table()
        self.create_collection_runs_table()
        print()

    # =========================================================================
    # IDEMPOTENCY: LOAD EXISTING IDS / LAST RUN DATE
    # =========================================================================

    def get_existing_press_release_ids(self) -> set:
        """
        Load press_release_ids that have been fully scraped (have a row in
        press_release_content).

        Used for URL-level deduplication BEFORE writing new records — prevents
        paying to re-scrape articles that already have content in the database.

        Intentionally checks press_release_content rather than
        press_release_metadata: a URL that was discovered (metadata written) but
        never successfully scraped (no content row) is NOT considered done, so it
        will be retried on the next run rather than silently skipped forever.

        Returns:
            Set of press_release_id strings that already have scraped content.
            Returns empty set on first run or if table doesn't exist.
        """
        table_id = self._get_table_ref("press_release_content")

        try:
            self.client.get_table(table_id)
        except NotFound:
            print("📝 press_release_content table not found (first run) — no existing IDs")
            return set()

        query = f"SELECT press_release_id FROM `{table_id}`"

        try:
            results = self.client.query(query).result()
            ids = {row.press_release_id for row in results}
            print(f"📝 Loaded {len(ids):,} existing press release IDs from BigQuery")
            return ids
        except Exception as e:
            print(f"⚠️  Error loading existing press release IDs: {e}")
            return set()

    def get_last_successful_run_end_date(self) -> Optional[str]:
        """
        Get the end_date from the most recently completed pipeline run.

        This is the primary mechanism for daily scheduling without hardcoded dates:
        the next run starts from this date (with a 1-day overlap for safety).

        Returns:
            Date string 'YYYY-MM-DD' of the last completed run's end_date,
            or None if no completed runs exist (e.g. first ever run).
        """
        table_id = self._get_table_ref("collection_runs")

        query = f"""
            SELECT FORMAT_DATE('%Y-%m-%d', end_date) AS end_date_str
            FROM `{table_id}`
            WHERE status = 'completed'
              AND end_timestamp IS NOT NULL
            ORDER BY end_timestamp DESC
            LIMIT 1
        """

        try:
            results = list(self.client.query(query).result())
            if results:
                end_date = results[0].end_date_str
                print(f"📅 Last successful run covered up to: {end_date}")
                return end_date
            print("📅 No completed runs found in collection_runs (first run or all failed)")
            return None
        except NotFound:
            print("📅 collection_runs table not found (first run)")
            return None
        except Exception as e:
            print(f"⚠️  Error getting last run end date: {e}")
            return None

    def get_collected_newsroom_urls(self) -> set:
        """
        Get all newsroom base URLs that have ever been collected.

        Used to detect truly new newsrooms that need historical backfill,
        replacing the broken previous approach that compared newsroom URLs
        against article URLs (they never matched).

        Returns:
            Set of newsroom_url strings (e.g. {'newsroom.apple.com', ...})
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"""
            SELECT DISTINCT newsroom_url
            FROM `{table_id}`
            WHERE newsroom_url IS NOT NULL AND newsroom_url != ''
        """

        try:
            results = self.client.query(query).result()
            urls = {row.newsroom_url for row in results}
            print(f"📝 Found {len(urls):,} newsroom URLs previously collected")
            return urls
        except NotFound:
            print("📝 press_release_metadata table not found — no previously collected newsrooms")
            return set()
        except Exception as e:
            print(f"⚠️  Error getting collected newsroom URLs: {e}")
            return set()

    # =========================================================================
    # WRITE METHODS (new split-table approach)
    # =========================================================================

    def write_press_release_metadata(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write press release metadata to BigQuery (press_release_metadata table).

        Analogous to insert_metadata_bq() in earnings-call-collector.
        One row per press release. Append-only; never updated after insertion.

        Expected DataFrame columns (extras are ignored):
            Required: url (or press_release_id already set)
            SERP:     title, description, rank, query
            Company:  company, newsroom_url
            Date:     publish_date

        Args:
            df:     DataFrame with press release metadata
            run_id: Optional run identifier

        Returns:
            Number of rows written.
        """
        if df.empty:
            print("⚠️  No press release metadata to write")
            return 0

        table_id = self._get_table_ref("press_release_metadata")
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Normalise url column name
        if 'url' not in df.columns and 'link' in df.columns:
            df = df.rename(columns={'link': 'url'})

        # Generate deterministic ID if not already present
        if 'press_release_id' not in df.columns:
            df['press_release_id'] = df['url'].apply(_generate_press_release_id)

        # Skip IDs already in press_release_metadata to prevent duplicate rows.
        # This is needed because get_existing_press_release_ids() now checks
        # press_release_content, so URLs discovered but never scraped will
        # pass SERP dedup and reach here a second time.
        try:
            existing_query = f"SELECT press_release_id FROM `{table_id}`"
            existing_meta_ids = {row.press_release_id
                                 for row in self.client.query(existing_query).result()}
            if existing_meta_ids:
                before = len(df)
                df = df[~df['press_release_id'].isin(existing_meta_ids)]
                skipped = before - len(df)
                if skipped:
                    print(f"📝 Skipped {skipped:,} metadata rows already in BigQuery")
        except Exception as e:
            print(f"⚠️  Could not check existing metadata IDs: {e} — proceeding anyway")

        if df.empty:
            print("⚠️  No new press release metadata to write")
            return 0

        # Convert publish_date to datetime if present
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        schema_columns = [
            'press_release_id', 'url', 'title', 'description', 'rank', 'query',
            'company', 'newsroom_url', 'publish_date', 'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job = self.client.load_table_from_dataframe(df_to_write, table_id, job_config=job_config)
        job.result()

        print(f"✓ Wrote {len(df_to_write):,} rows to press_release_metadata: {table_id}")
        return len(df_to_write)

    def update_metadata_publish_dates(self, df: pd.DataFrame) -> int:
        """
        Update publish_date on existing press_release_metadata rows.

        Called after scraping, which extracts publish dates that were not
        available in the original SERP results.

        Args:
            df: DataFrame with at least 'press_release_id' and 'publish_date' columns.

        Returns:
            Number of rows updated.
        """
        if df.empty or 'publish_date' not in df.columns:
            return 0

        df = df.copy()
        df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Only rows that actually have a scraped publish_date
        has_date = df[df['publish_date'].notna()].copy()
        if has_date.empty:
            return 0

        table_id = self._get_table_ref("press_release_metadata")
        updated = 0

        for _, row in has_date.iterrows():
            pid = row['press_release_id']
            ts = row['publish_date'].strftime('%Y-%m-%d %H:%M:%S')
            query = f"""
                UPDATE `{table_id}`
                SET publish_date = TIMESTAMP('{ts}')
                WHERE press_release_id = '{pid}'
                  AND publish_date IS NULL
            """
            try:
                result = self.client.query(query).result()
                updated += result.num_dml_affected_rows or 0
            except Exception:
                pass  # best-effort

        if updated:
            print(f"✓ Updated publish_date on {updated:,} metadata rows")
        return updated

    def write_press_release_content(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write press release content to BigQuery (press_release_content table).

        Analogous to insert_content_bq() in earnings-call-collector.
        Only rows where article_text is non-empty are written.

        Expected DataFrame columns (extras are ignored):
            Required: press_release_id (or url, from which ID is derived)
            Content:  article_text, summary, keywords, scraper_used

        Args:
            df:     DataFrame with scraped article content
            run_id: Optional run identifier

        Returns:
            Number of rows written.
        """
        if df.empty:
            print("⚠️  No press release content to write")
            return 0

        table_id = self._get_table_ref("press_release_content")
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Derive press_release_id from url if not already present
        if 'press_release_id' not in df.columns:
            if 'url' in df.columns:
                df['press_release_id'] = df['url'].apply(_generate_press_release_id)
            else:
                raise ValueError("DataFrame must have 'press_release_id' or 'url' column")

        # Only write rows with actual scraped content
        has_content = df['article_text'].notna() & (df['article_text'].str.strip() != '')
        df = df[has_content]

        if df.empty:
            print("⚠️  No scraped content to write (all article_text values are empty/null)")
            return 0

        schema_columns = [
            'press_release_id', 'article_text', 'summary', 'keywords',
            'scraper_used', 'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job = self.client.load_table_from_dataframe(df_to_write, table_id, job_config=job_config)
        job.result()

        print(f"✓ Wrote {len(df_to_write):,} rows to press_release_content: {table_id}")
        return len(df_to_write)

    # =========================================================================
    # RUN LOGGING
    # =========================================================================

    def log_run_start(self, run_id: str, start_date: str, end_date: str,
                      companies: List[str] = None) -> None:
        """
        Log the start of a collection run.

        Args:
            run_id:     Unique run identifier
            start_date: Start date (YYYY-MM-DD)
            end_date:   End date (YYYY-MM-DD)
            companies:  List of company identifiers being processed
        """
        table_id = self._get_table_ref("collection_runs")

        row = {
            'run_id': run_id,
            'start_date': datetime.strptime(start_date, '%Y-%m-%d').date(),
            'end_date': datetime.strptime(end_date, '%Y-%m-%d').date(),
            'companies_processed': companies or [],
            'urls_collected': 0,
            'articles_scraped': 0,
            'status': 'started',
            'start_timestamp': datetime.utcnow(),
            'end_timestamp': None,
            'error_message': None
        }

        df = pd.DataFrame([row])
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        print(f"✓ Logged run start: {run_id}")

    def log_run_completion(self, run_id: str, urls_collected: int = 0,
                           articles_scraped: int = 0,
                           queries_executed: List[str] = None,
                           error_message: str = None) -> None:
        """
        Update a collection run with completion status.

        Args:
            run_id:            Unique run identifier
            urls_collected:    Number of URLs collected
            articles_scraped:  Number of articles scraped
            queries_executed:  List of query strings that were executed
            error_message:     Error message if failed
        """
        table_id = self._get_table_ref("collection_runs")

        status = 'failed' if error_message else 'completed'

        # Prepare queries array for SQL
        queries_array = 'NULL'
        queries_count = 0
        if queries_executed:
            queries_count = len(queries_executed)
            escaped_queries = [q.replace("'", "\\'") for q in queries_executed]
            queries_array = '[' + ','.join([f"'{q}'" for q in escaped_queries]) + ']'

        query = f"""
            UPDATE `{table_id}`
            SET
                status = '{status}',
                queries_executed = {queries_array},
                queries_count = {queries_count},
                urls_collected = {urls_collected},
                articles_scraped = {articles_scraped},
                end_timestamp = CURRENT_TIMESTAMP(),
                error_message = {f"'{error_message}'" if error_message else 'NULL'}
            WHERE run_id = '{run_id}'
        """

        self.client.query(query).result()
        print(f"✓ Logged run completion: {run_id} ({status})")

    # =========================================================================
    # QUERY-LEVEL DEDUPLICATION (SERP API cost savings)
    # =========================================================================

    def get_executed_queries_for_date_range(self, start_date: str, end_date: str) -> set:
        """
        Get search queries already executed for overlapping date ranges.

        Enables query-level deduplication BEFORE hitting the SERP API,
        preventing unnecessary API costs. Checked in run_serp_collection()
        as the first line of defence against redundant work.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date:   End date (YYYY-MM-DD)

        Returns:
            Set of query strings already executed that overlap with this date range.
        """
        table_id = self._get_table_ref("collection_runs")

        query = f"""
            SELECT DISTINCT query
            FROM `{table_id}`,
            UNNEST(queries_executed) AS query
            WHERE status = 'completed'
              AND start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
              AND start_date <= DATE('{end_date}')
              AND end_date >= DATE('{start_date}')
        """

        try:
            results = self.client.query(query).result(timeout=60)
            queries = {row.query for row in results}
            print(f"📝 Found {len(queries):,} queries already executed for overlapping date ranges")
            return queries
        except NotFound:
            print(f"⚠️  Table not found: {table_id} (first run)")
            return set()
        except Exception as e:
            print(f"⚠️  Error checking executed queries: {e}")
            return set()

    # =========================================================================
    # BACKFILL / URL CHECKS
    # =========================================================================

    def identify_urls_needing_backfill(self, current_urls: List[str],
                                        backfill_start_date: str = "2026-01-01") -> Dict[str, str]:
        """
        [LEGACY] Identify newsroom URLs that are new and need historical backfill.

        Previously compared newsroom URLs against article URLs (a bug — they never
        matched). Use get_collected_newsroom_urls() + set difference instead, which
        is what run_serp_collection() now does directly.

        Kept for backwards compatibility only.
        """
        collected_newsrooms = self.get_collected_newsroom_urls()
        new_urls = set(current_urls) - collected_newsrooms

        if new_urls:
            print(f"🆕 Found {len(new_urls)} new newsrooms needing backfill from {backfill_start_date}")
            return {url: backfill_start_date for url in new_urls}
        else:
            print("✓ No new newsrooms needing backfill")
            return {}

    def get_collected_urls_for_date_range(self, start_date: str, end_date: str) -> set:
        """
        Get URLs already collected in a specific date range (by collection timestamp).

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date:   End date (YYYY-MM-DD)

        Returns:
            Set of URLs already collected.
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"""
            SELECT DISTINCT url
            FROM `{table_id}`
            WHERE DATE(collection_timestamp) >= DATE('{start_date}')
              AND DATE(collection_timestamp) <= DATE('{end_date}')
        """

        try:
            results = self.client.query(query).result()
            urls = {row.url for row in results}
            print(f"📝 Found {len(urls):,} URLs already collected for {start_date} to {end_date}")
            return urls
        except Exception as e:
            print(f"⚠️  Error checking collected URLs: {e}")
            return set()

    def get_all_collected_urls(self) -> set:
        """
        Get all URLs that have ever been collected (from press_release_metadata).

        Returns:
            Set of all collected URLs.
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"SELECT DISTINCT url FROM `{table_id}`"

        try:
            results = self.client.query(query).result()
            urls = {row.url for row in results}
            print(f"📝 Found {len(urls):,} total URLs in press_release_metadata")
            return urls
        except NotFound:
            print(f"⚠️  Table not found: {table_id} (first run)")
            return set()
        except Exception as e:
            print(f"⚠️  Error checking collected URLs: {e}")
            return set()

    def get_processed_urls(self, days_back: int = 30) -> set:
        """
        Get URLs collected in the last N days.

        Args:
            days_back: Number of days to look back

        Returns:
            Set of processed URLs.
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"""
            SELECT DISTINCT url
            FROM `{table_id}`
            WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days_back} DAY)
        """

        try:
            results = self.client.query(query).result()
            urls = {row.url for row in results}
            print(f"📝 Found {len(urls):,} processed URLs from last {days_back} days")
            return urls
        except NotFound:
            print(f"⚠️  Table not found: {table_id}")
            return set()

    # =========================================================================
    # LEGACY: write_collected_articles (no longer called by pipeline)
    # =========================================================================

    def write_collected_articles(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        [LEGACY] Write to the original combined collected_articles table.

        This method is no longer called by the pipeline. New data is written to
        press_release_metadata + press_release_content instead. Kept here so
        any external scripts referencing this method do not break.
        """
        if df.empty:
            print("⚠️  No articles to write")
            return 0

        table_id = self._get_table_ref("collected_articles")

        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        if 'url' not in df.columns:
            if 'link' in df.columns:
                df = df.rename(columns={'link': 'url'})
            else:
                raise ValueError("DataFrame must have 'url' or 'link' column")

        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        schema_columns = [
            'url', 'title', 'description', 'rank', 'query',
            'article_text', 'summary', 'keywords', 'publish_date', 'scraper_used',
            'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df_to_write, table_id, job_config=job_config)
        job.result()

        print(f"✓ [LEGACY] Wrote {len(df_to_write):,} collected articles to BigQuery: {table_id}")
        return len(df_to_write)


if __name__ == "__main__":
    # Smoke test: create tables and verify they exist
    storage = BigQueryStorage()
    storage.initialize_tables()

    print("\n✅ BigQuery storage initialisation complete")
    print("Tables created/verified:")
    print("  • press_release_metadata  (one row per press release)")
    print("  • press_release_content   (scraped text for each release)")
    print("  • collection_runs         (pipeline run log)")
