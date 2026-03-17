"""
BigQuery Storage Module
========================

Manages ALL BigQuery table operations for the press release collection pipeline.

Schema Design (3 tables):
--------------------------
1. press_release_metadata
   - One row per press release found by SERP search.
   - Contains: URL, title, description, SERP rank, company, sector, newsroom_url,
     publish_date, and the query that found it.
   - Immutable once inserted (except publish_date, which is backfilled after scraping).
   - Partitioned by collection_timestamp (day), clustered by company + press_release_id.

2. press_release_content
   - One row per SUCCESSFULLY scraped press release.
   - Contains: article_text and which scraper extracted it.
   - Joined to metadata via press_release_id (MD5 of URL).
   - Partitioned by collection_timestamp (day), clustered by press_release_id.

3. collection_runs
   - One row per pipeline execution (both Cloud Run and CLI).
   - Tracks date range, queries executed, counts, status, and timing.
   - Powers idempotency: get_last_successful_run_end_date() tells the next
     Cloud Run invocation where to start collecting from.

ID Strategy:
  press_release_id = MD5(url)
  Same URL always produces the same ID, enabling idempotent inserts.
  This mirrors the MD5(symbol + report_date) pattern in earnings-call-collector.

Key Priority Metadata (per user requirements):
  - company:  Identified by matching the site:url in the SERP query against
              the benchmarking_corporate_reference table in BigQuery.
  - sector:   Also from benchmarking_corporate_reference (flows through
              reference_data.csv into the pipeline).
  - publish_date: Extracted by the scraper from the article text/HTML/URL.
"""

import hashlib
from datetime import datetime
from typing import Optional, List, Dict

import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from config import config


# ---------------------------------------------------------------------------
# Helper: deterministic press_release_id from URL
# ---------------------------------------------------------------------------

def _generate_press_release_id(url: str) -> str:
    """
    Generate a deterministic press_release_id from a URL using MD5.

    This is the primary key linking press_release_metadata and
    press_release_content tables. Same URL always produces the same ID,
    so re-running the pipeline for the same articles is safe (idempotent).

    Analogous to MD5(symbol + report_date) in earnings-call-collector.
    """
    return hashlib.md5(str(url).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Main storage class
# ---------------------------------------------------------------------------

class BigQueryStorage:
    """
    Handle all BigQuery operations for the press release collection pipeline.

    Usage:
        storage = BigQueryStorage()
        storage.initialize_tables()       # Create tables if they don't exist
        storage.write_press_release_metadata(df, run_id="...")
        storage.write_press_release_content(df, run_id="...")
    """

    def __init__(self, project_id: str = None, dataset_id: str = None):
        """
        Initialize BigQuery storage.

        Args:
            project_id: GCP project ID. Defaults to the project set in gcloud CLI
                        or the service account's project.
            dataset_id: BigQuery dataset name. Defaults to config.BIGQUERY_DATASET
                        (usually "pressure_monitoring").
        """
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id or self.client.project
        self.dataset_id = dataset_id or config.BIGQUERY_DATASET
        self.dataset_ref = f"{self.project_id}.{self.dataset_id}"

        # Create the dataset if it doesn't exist yet (first-time setup)
        self._ensure_dataset_exists()

    def _ensure_dataset_exists(self):
        """Create the BigQuery dataset if it doesn't already exist."""
        try:
            self.client.get_dataset(self.dataset_ref)
            print(f"Using BigQuery dataset: {self.dataset_ref}")
        except NotFound:
            dataset = bigquery.Dataset(self.dataset_ref)
            dataset.location = "US"
            dataset = self.client.create_dataset(dataset)
            print(f"Created BigQuery dataset: {self.dataset_ref}")

    def _get_table_ref(self, table_name: str) -> str:
        """Get fully qualified table reference (project.dataset.table)."""
        return f"{self.dataset_ref}.{table_name}"

    # =========================================================================
    # TABLE CREATION
    # =========================================================================

    def create_press_release_metadata_table(self):
        """
        Create the press_release_metadata table.

        This table stores one row per press release discovered by SERP search.
        It is the "wide" table containing all metadata about each press release.

        Key columns for the user's priority metadata:
          - company:      Corporation name (e.g. "Apple Inc.")
          - sector:       Industry sector (e.g. "Technology") from BQ reference table
          - publish_date: Article publication date (backfilled after scraping)

        Partitioned by collection_timestamp for efficient time-range queries.
        Clustered by company (most common filter) then press_release_id.
        """
        table_id = self._get_table_ref("press_release_metadata")

        schema = [
            # -- Primary key (deterministic MD5 of URL) --
            bigquery.SchemaField(
                "press_release_id", "STRING", mode="REQUIRED",
                description="MD5(url) -- deterministic ID linking to content table"
            ),
            bigquery.SchemaField(
                "url", "STRING", mode="REQUIRED",
                description="Full article URL"
            ),

            # -- SERP fields (from Google search results) --
            bigquery.SchemaField(
                "title", "STRING", mode="NULLABLE",
                description="Article title (scraped page title when available, else SERP title)"
            ),
            bigquery.SchemaField(
                "description", "STRING", mode="NULLABLE",
                description="Meta description snippet from SERP"
            ),
            bigquery.SchemaField(
                "rank", "INTEGER", mode="NULLABLE",
                description="Search result rank position (1 = top result)"
            ),
            bigquery.SchemaField(
                "query", "STRING", mode="NULLABLE",
                description="Full Google search query URL that found this article"
            ),

            # -- Company context (from benchmarking_corporate_reference) --
            bigquery.SchemaField(
                "company", "STRING", mode="NULLABLE",
                description="Corporation name (e.g. 'Apple Inc.') from reference data"
            ),
            bigquery.SchemaField(
                "sector", "STRING", mode="NULLABLE",
                description="Industry sector (e.g. 'Technology') from reference data"
            ),
            bigquery.SchemaField(
                "newsroom_url", "STRING", mode="NULLABLE",
                description="Newsroom base URL used in site: search (e.g. newsroom.apple.com)"
            ),

            # -- Dates --
            bigquery.SchemaField(
                "publish_date", "TIMESTAMP", mode="NULLABLE",
                description="Article publication date (scraped from article when available)"
            ),
            bigquery.SchemaField(
                "collection_timestamp", "TIMESTAMP", mode="REQUIRED",
                description="When this article was collected by the pipeline"
            ),
            bigquery.SchemaField(
                "run_id", "STRING", mode="NULLABLE",
                description="Pipeline run identifier (YYYYMMDD_HHMMSS)"
            ),
        ]

        table = bigquery.Table(table_id, schema=schema)

        # Partition by day on collection_timestamp for efficient time-range queries
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )

        # Cluster by company first (most common filter), then press_release_id
        table.clustering_fields = ["company", "press_release_id"]

        try:
            self.client.get_table(table_id)
            print(f"Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"Created table: {table_id}")

    def create_press_release_content_table(self):
        """
        Create the press_release_content table.

        This table stores the actual scraped article text. Only rows where
        scraping succeeded have entries here. Joined to metadata via
        press_release_id.

        This is a collection-only table: no summaries, no keywords, no analysis.
        """
        table_id = self._get_table_ref("press_release_content")

        schema = [
            # -- Foreign key to metadata table --
            bigquery.SchemaField(
                "press_release_id", "STRING", mode="REQUIRED",
                description="MD5(url) -- foreign key to press_release_metadata"
            ),

            # -- Scraped content --
            bigquery.SchemaField(
                "article_text", "STRING", mode="NULLABLE",
                description="Full article text extracted by the scraper"
            ),
            bigquery.SchemaField(
                "scraper_used", "STRING", mode="NULLABLE",
                description="Which scraper succeeded (newspaper3k, trafilatura, etc.)"
            ),

            # -- Metadata --
            bigquery.SchemaField(
                "collection_timestamp", "TIMESTAMP", mode="REQUIRED",
                description="When content was scraped"
            ),
            bigquery.SchemaField(
                "run_id", "STRING", mode="NULLABLE",
                description="Pipeline run identifier"
            ),
        ]

        table = bigquery.Table(table_id, schema=schema)

        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )
        table.clustering_fields = ["press_release_id"]

        try:
            self.client.get_table(table_id)
            print(f"Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"Created table: {table_id}")

    def create_collection_runs_table(self):
        """
        Create the collection_runs table.

        This table logs every pipeline execution (both Cloud Run and CLI).
        It enables two critical features:

        1. Auto date detection: get_last_successful_run_end_date() reads this
           table to determine where the next Cloud Run invocation should start.
           This means Cloud Run needs zero configuration -- it just picks up
           where the last run left off.

        2. Query-level deduplication: get_executed_queries_for_date_range()
           checks which SERP queries have already been run for overlapping
           date ranges, saving API costs by not re-running them.
        """
        table_id = self._get_table_ref("collection_runs")

        schema = [
            bigquery.SchemaField(
                "run_id", "STRING", mode="REQUIRED",
                description="Unique run identifier (YYYYMMDD_HHMMSS)"
            ),
            bigquery.SchemaField(
                "start_date", "DATE", mode="REQUIRED",
                description="Start of the date range collected"
            ),
            bigquery.SchemaField(
                "end_date", "DATE", mode="REQUIRED",
                description="End of the date range collected"
            ),
            bigquery.SchemaField(
                "companies_processed", "STRING", mode="REPEATED",
                description="List of company names processed in this run"
            ),
            bigquery.SchemaField(
                "queries_executed", "STRING", mode="REPEATED",
                description="List of SERP query URLs executed (for query-level dedup)"
            ),
            bigquery.SchemaField(
                "queries_count", "INTEGER", mode="NULLABLE",
                description="Number of queries executed"
            ),
            bigquery.SchemaField(
                "urls_collected", "INTEGER", mode="NULLABLE",
                description="Number of unique URLs collected from SERP"
            ),
            bigquery.SchemaField(
                "articles_scraped", "INTEGER", mode="NULLABLE",
                description="Number of articles successfully scraped"
            ),
            bigquery.SchemaField(
                "status", "STRING", mode="REQUIRED",
                description="Run status: 'started', 'completed', or 'failed'"
            ),
            bigquery.SchemaField(
                "start_timestamp", "TIMESTAMP", mode="REQUIRED",
                description="When the pipeline run started"
            ),
            bigquery.SchemaField(
                "end_timestamp", "TIMESTAMP", mode="NULLABLE",
                description="When the pipeline run completed or failed"
            ),
            bigquery.SchemaField(
                "error_message", "STRING", mode="NULLABLE",
                description="Error message if the run failed"
            ),
        ]

        table = bigquery.Table(table_id, schema=schema)

        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="start_timestamp"
        )

        try:
            self.client.get_table(table_id)
            print(f"Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"Created table: {table_id}")

    def initialize_tables(self):
        """
        Create all required tables if they don't already exist.

        Safe to call on every run -- existing tables are left untouched.
        Called at the start of both Cloud Run and CLI pipelines.
        """
        print("\nInitializing BigQuery tables...")
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

        Used for URL-level deduplication BEFORE writing new records. This
        prevents paying to re-scrape articles that already have content.

        Why check content table, not metadata?
          A URL that was discovered (metadata written) but never successfully
          scraped (no content row) should be RETRIED on the next run, not
          silently skipped forever.

        Returns:
            Set of press_release_id strings. Empty set on first run.
        """
        table_id = self._get_table_ref("press_release_content")

        try:
            self.client.get_table(table_id)
        except NotFound:
            print("press_release_content table not found (first run) -- no existing IDs")
            return set()

        query = f"SELECT press_release_id FROM `{table_id}`"

        try:
            results = self.client.query(query).result()
            ids = {row.press_release_id for row in results}
            print(f"Loaded {len(ids):,} existing press release IDs from BigQuery")
            return ids
        except Exception as e:
            print(f"Warning: Error loading existing IDs: {e}")
            return set()

    def get_last_successful_run_end_date(self) -> Optional[str]:
        """
        Get the end_date from the most recently completed pipeline run.

        This is the PRIMARY mechanism for daily scheduling without hardcoded dates:
        Cloud Run calls this to figure out where to start collecting from.
        The next run starts from this date (with 1-day overlap for safety).

        Returns:
            Date string 'YYYY-MM-DD', or None if no completed runs exist
            (e.g. first ever run).
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
                print(f"Last successful run covered up to: {end_date}")
                return end_date
            print("No completed runs found (first run or all failed)")
            return None
        except NotFound:
            print("collection_runs table not found (first run)")
            return None
        except Exception as e:
            print(f"Warning: Error getting last run end date: {e}")
            return None

    def get_collected_newsroom_urls(self) -> set:
        """
        Get all newsroom base URLs that have ever been collected.

        Used to detect NEW newsrooms that need historical backfill.
        When a new company is added to the reference data, the pipeline
        detects it here and backfills from a historical start date
        (rather than only collecting from the current run's start date).

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
            print(f"Found {len(urls):,} newsroom URLs previously collected")
            return urls
        except NotFound:
            print("press_release_metadata table not found -- no previously collected newsrooms")
            return set()
        except Exception as e:
            print(f"Warning: Error getting collected newsroom URLs: {e}")
            return set()

    # =========================================================================
    # WRITE METHODS
    # =========================================================================

    def write_press_release_metadata(self, df: pd.DataFrame,
                                     run_id: str = None) -> int:
        """
        Write press release metadata to BigQuery (press_release_metadata table).

        One row per press release. Append-only; never updated after insertion
        (except publish_date, which is backfilled by update_metadata_publish_dates).

        Before writing, checks for existing press_release_ids in the metadata
        table and skips duplicates to prevent double-counting.

        Expected DataFrame columns (extras are ignored):
            Required: url (or press_release_id already set)
            SERP:     title, description, rank, query
            Company:  company, sector, newsroom_url
            Date:     publish_date

        Args:
            df:     DataFrame with press release metadata.
            run_id: Pipeline run identifier (YYYYMMDD_HHMMSS).

        Returns:
            Number of rows written.
        """
        if df.empty:
            print("No press release metadata to write")
            return 0

        table_id = self._get_table_ref("press_release_metadata")
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Normalize the URL column name (SERP module uses 'link')
        if 'url' not in df.columns and 'link' in df.columns:
            df = df.rename(columns={'link': 'url'})

        # Generate deterministic ID if not already present
        if 'press_release_id' not in df.columns:
            df['press_release_id'] = df['url'].apply(_generate_press_release_id)

        # -- Dedup against existing metadata rows --
        # This prevents duplicate rows when the same URL is discovered
        # across multiple runs or overlapping date ranges.
        try:
            existing_query = f"SELECT press_release_id FROM `{table_id}`"
            existing_meta_ids = {
                row.press_release_id
                for row in self.client.query(existing_query).result()
            }
            if existing_meta_ids:
                before = len(df)
                df = df[~df['press_release_id'].isin(existing_meta_ids)]
                skipped = before - len(df)
                if skipped:
                    print(f"Skipped {skipped:,} metadata rows already in BigQuery")
        except Exception as e:
            print(f"Warning: Could not check existing metadata IDs: {e} -- proceeding anyway")

        if df.empty:
            print("No new press release metadata to write")
            return 0

        # Convert publish_date to datetime (handles various string formats)
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Only write columns that match the table schema
        schema_columns = [
            'press_release_id', 'url', 'title', 'description', 'rank', 'query',
            'company', 'sector', 'newsroom_url', 'publish_date',
            'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job = self.client.load_table_from_dataframe(
            df_to_write, table_id, job_config=job_config
        )
        job.result()  # Wait for the load job to complete

        print(f"Wrote {len(df_to_write):,} rows to press_release_metadata")
        return len(df_to_write)

    def update_metadata_publish_dates(self, df: pd.DataFrame) -> int:
        """
        Update publish_date on existing metadata rows.

        Called AFTER scraping, because scrapers extract publish dates that
        were not available in the original SERP results. Only updates rows
        where publish_date is currently NULL (doesn't overwrite existing dates).

        Args:
            df: DataFrame with at least 'press_release_id' and 'publish_date'.

        Returns:
            Number of rows updated.
        """
        if df.empty or 'publish_date' not in df.columns:
            return 0

        df = df.copy()
        df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Only process rows that actually have a scraped publish_date
        has_date = df[df['publish_date'].notna()].copy()
        if has_date.empty:
            return 0

        table_id = self._get_table_ref("press_release_metadata")
        updated = 0

        # Update each row individually (BQ doesn't support batch UPDATE easily).
        # Only sets publish_date where it's currently NULL to avoid overwriting
        # any manually-corrected dates.
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
                pass  # Best-effort: don't fail the pipeline over a date update

        if updated:
            print(f"Updated publish_date on {updated:,} metadata rows")
        return updated

    def write_press_release_content(self, df: pd.DataFrame,
                                    run_id: str = None) -> int:
        """
        Write press release content to BigQuery (press_release_content table).

        Only writes rows where article_text is non-empty. This table stores
        the actual scraped article text -- no summaries, no keywords, just
        the raw collected text and which scraper extracted it.

        Args:
            df:     DataFrame with scraped article content.
            run_id: Pipeline run identifier.

        Returns:
            Number of rows written.
        """
        if df.empty:
            print("No press release content to write")
            return 0

        table_id = self._get_table_ref("press_release_content")
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Derive press_release_id from URL if not already present
        if 'press_release_id' not in df.columns:
            if 'url' in df.columns:
                df['press_release_id'] = df['url'].apply(_generate_press_release_id)
            else:
                raise ValueError("DataFrame must have 'press_release_id' or 'url' column")

        # Only write rows that actually have scraped content
        has_content = df['article_text'].notna() & (df['article_text'].str.strip() != '')
        df = df[has_content]

        if df.empty:
            print("No scraped content to write (all article_text values are empty/null)")
            return 0

        # Only write columns that match the table schema (no summary/keywords)
        schema_columns = [
            'press_release_id', 'article_text', 'scraper_used',
            'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job = self.client.load_table_from_dataframe(
            df_to_write, table_id, job_config=job_config
        )
        job.result()

        print(f"Wrote {len(df_to_write):,} rows to press_release_content")
        return len(df_to_write)

    # =========================================================================
    # RUN LOGGING
    # =========================================================================

    def log_run_start(self, run_id: str, start_date: str, end_date: str,
                      companies: List[str] = None) -> None:
        """
        Log the START of a pipeline run.

        Called at the beginning of both Cloud Run and CLI pipelines.
        The run status is set to 'started' and will be updated to
        'completed' or 'failed' by log_run_completion().

        Args:
            run_id:     Unique run identifier (YYYYMMDD_HHMMSS).
            start_date: Start of the date range being collected (YYYY-MM-DD).
            end_date:   End of the date range being collected (YYYY-MM-DD).
            companies:  List of company names being processed.
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
        print(f"Logged run start: {run_id}")

    def log_run_completion(self, run_id: str, urls_collected: int = 0,
                           articles_scraped: int = 0,
                           queries_executed: List[str] = None,
                           error_message: str = None) -> None:
        """
        Update a pipeline run with completion status.

        Called at the end of both Cloud Run and CLI pipelines (whether
        the run succeeded or failed). Updates the row created by log_run_start().

        Args:
            run_id:           Unique run identifier.
            urls_collected:   Number of unique URLs collected from SERP.
            articles_scraped: Number of articles successfully scraped.
            queries_executed: List of SERP query URLs that were executed.
            error_message:    Error message if the run failed (None = success).
        """
        table_id = self._get_table_ref("collection_runs")

        status = 'failed' if error_message else 'completed'

        # Build the queries array for the SQL UPDATE statement
        queries_array = 'NULL'
        queries_count = 0
        if queries_executed:
            queries_count = len(queries_executed)
            # Escape single quotes in query strings to prevent SQL injection
            escaped = [q.replace("'", "\\'") for q in queries_executed]
            queries_array = '[' + ','.join([f"'{q}'" for q in escaped]) + ']'

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
        print(f"Logged run completion: {run_id} ({status})")

    # =========================================================================
    # QUERY-LEVEL DEDUPLICATION (saves SERP API costs)
    # =========================================================================

    def get_executed_queries_for_date_range(self, start_date: str,
                                            end_date: str) -> set:
        """
        Get SERP queries already executed for overlapping date ranges.

        This is the FIRST line of defense against redundant SERP API calls.
        Before sending a query to Bright Data (which costs money), we check
        if that exact query was already executed in a recent completed run
        that covered an overlapping date range.

        Only looks back 90 days to keep the query fast and avoid matching
        very old runs whose data may be stale.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date:   End date (YYYY-MM-DD).

        Returns:
            Set of query URL strings already executed for overlapping ranges.
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
            print(f"Found {len(queries):,} queries already executed for overlapping ranges")
            return queries
        except NotFound:
            print(f"Table not found: {table_id} (first run)")
            return set()
        except Exception as e:
            print(f"Warning: Error checking executed queries: {e}")
            return set()

    # =========================================================================
    # UTILITY QUERIES
    # =========================================================================

    def get_collected_urls_for_date_range(self, start_date: str,
                                          end_date: str) -> set:
        """
        Get URLs already collected within a specific date range.

        Useful for manual dedup checks and debugging.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date:   End date (YYYY-MM-DD).

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
            print(f"Found {len(urls):,} URLs collected for {start_date} to {end_date}")
            return urls
        except Exception as e:
            print(f"Warning: Error checking collected URLs: {e}")
            return set()

    def get_all_collected_urls(self) -> set:
        """
        Get ALL URLs ever collected (from press_release_metadata).

        Returns:
            Set of all collected URL strings.
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"SELECT DISTINCT url FROM `{table_id}`"

        try:
            results = self.client.query(query).result()
            urls = {row.url for row in results}
            print(f"Found {len(urls):,} total URLs in press_release_metadata")
            return urls
        except NotFound:
            print(f"Table not found: {table_id} (first run)")
            return set()
        except Exception as e:
            print(f"Warning: Error checking collected URLs: {e}")
            return set()

    def get_processed_urls(self, days_back: int = 30) -> set:
        """
        Get URLs collected in the last N days.

        Useful for quick recency checks without scanning the full table.

        Args:
            days_back: Number of days to look back.

        Returns:
            Set of URL strings.
        """
        table_id = self._get_table_ref("press_release_metadata")

        query = f"""
            SELECT DISTINCT url
            FROM `{table_id}`
            WHERE collection_timestamp >= TIMESTAMP_SUB(
                CURRENT_TIMESTAMP(), INTERVAL {days_back} DAY
            )
        """

        try:
            results = self.client.query(query).result()
            urls = {row.url for row in results}
            print(f"Found {len(urls):,} URLs from last {days_back} days")
            return urls
        except NotFound:
            print(f"Table not found: {table_id}")
            return set()


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    storage = BigQueryStorage()
    storage.initialize_tables()

    print("\nBigQuery storage initialization complete")
    print("Tables created/verified:")
    print("  - press_release_metadata  (one row per press release)")
    print("  - press_release_content   (scraped text for each release)")
    print("  - collection_runs         (pipeline run log)")
