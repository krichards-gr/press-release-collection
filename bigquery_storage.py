"""
BigQuery Storage Module
========================

Manages BigQuery table operations for the press release collection pipeline.

Schema Design:
--------------
1. collected_articles: Raw SERP + scraped content (immutable once collected)
2. article_enrichments: URL + analysis results (can be regenerated/updated)

This separation allows re-running enrichments without re-scraping articles.
"""

import os
from datetime import datetime
from typing import Optional, List
import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from config import config


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

    def create_collected_articles_table(self):
        """
        Create table for collected articles (SERP + scraped content).

        This table stores all raw article data and is immutable once collected.
        Combines SERP metadata with full article content.
        """
        table_id = self._get_table_ref("collected_articles")

        schema = [
            # SERP fields
            bigquery.SchemaField("url", "STRING", mode="REQUIRED", description="Article URL (primary key)"),
            bigquery.SchemaField("title", "STRING", mode="NULLABLE", description="Article title from SERP"),
            bigquery.SchemaField("description", "STRING", mode="NULLABLE", description="Meta description from SERP"),
            bigquery.SchemaField("rank", "INTEGER", mode="NULLABLE", description="Search result rank position"),
            bigquery.SchemaField("query", "STRING", mode="NULLABLE", description="Search query that found this article"),

            # Scraped content fields
            bigquery.SchemaField("article_text", "STRING", mode="NULLABLE", description="Full article text"),
            bigquery.SchemaField("summary", "STRING", mode="NULLABLE", description="Auto-generated summary"),
            bigquery.SchemaField("keywords", "STRING", mode="NULLABLE", description="Extracted keywords (comma-separated)"),
            bigquery.SchemaField("publish_date", "TIMESTAMP", mode="NULLABLE", description="Article publication date"),
            bigquery.SchemaField("scraper_used", "STRING", mode="NULLABLE", description="Which scraper successfully extracted content"),

            # Metadata
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP", mode="REQUIRED", description="When article was collected"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE", description="Pipeline run identifier"),
        ]

        table = bigquery.Table(table_id, schema=schema)

        # Partition by collection date for efficient querying
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )

        # Cluster by URL for efficient lookups
        table.clustering_fields = ["url"]

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_article_enrichments_table(self):
        """
        Create table for article enrichments (sentiment, entities, issues).

        This table stores analysis results that can be regenerated.
        URL is the primary key to join back to collected_articles.
        """
        table_id = self._get_table_ref("article_enrichments")

        schema = [
            # Primary key
            bigquery.SchemaField("url", "STRING", mode="REQUIRED", description="Article URL (foreign key to collected_articles)"),

            # Current enrichments
            bigquery.SchemaField("sentiment", "STRING", mode="NULLABLE", description="Sentiment label: positive, negative, neutral"),
            bigquery.SchemaField("sentiment_score", "FLOAT", mode="NULLABLE", description="Sentiment confidence score"),

            # Future enrichments (placeholder fields)
            bigquery.SchemaField("issue_labels", "STRING", mode="REPEATED", description="Identified issues/topics"),
            bigquery.SchemaField("entity_labels", "STRING", mode="REPEATED", description="Named entities mentioned"),
            bigquery.SchemaField("custom_metadata", "JSON", mode="NULLABLE", description="Additional metadata as JSON"),

            # Metadata
            bigquery.SchemaField("enrichment_timestamp", "TIMESTAMP", mode="REQUIRED", description="When enrichments were generated"),
            bigquery.SchemaField("enrichment_version", "STRING", mode="NULLABLE", description="Version of enrichment pipeline"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE", description="Pipeline run identifier"),
        ]

        table = bigquery.Table(table_id, schema=schema)

        # Partition by enrichment date
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="enrichment_timestamp"
        )

        # Cluster by URL for efficient joins
        table.clustering_fields = ["url"]

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def initialize_tables(self):
        """Create all required tables."""
        print("\n📊 Initializing BigQuery tables...")
        self.create_collected_articles_table()
        self.create_article_enrichments_table()
        print()

    def write_collected_articles(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write collected articles (SERP + scraped content) to BigQuery.

        This combines SERP metadata with scraped article content.
        Expected DataFrame columns:
        - SERP: url, title, description, rank, query
        - Scraped: article_text, summary, keywords, publish_date, scraper_used

        Args:
            df: DataFrame with combined SERP + article data
            run_id: Optional run identifier

        Returns:
            Number of rows written
        """
        if df.empty:
            print("⚠️  No articles to write")
            return 0

        table_id = self._get_table_ref("collected_articles")

        # Prepare data
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Ensure required column exists
        if 'url' not in df.columns:
            if 'link' in df.columns:
                df = df.rename(columns={'link': 'url'})
            else:
                raise ValueError("DataFrame must have 'url' or 'link' column")

        # Convert publish_date to datetime
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Select only columns that exist in schema
        schema_columns = [
            'url', 'title', 'description', 'rank', 'query',
            'article_text', 'summary', 'keywords', 'publish_date', 'scraper_used',
            'collection_timestamp', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        # Write to BigQuery (append mode)
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df_to_write, table_id, job_config=job_config)
        job.result()  # Wait for completion

        print(f"✓ Wrote {len(df_to_write):,} collected articles to BigQuery: {table_id}")
        return len(df_to_write)

    def write_article_enrichments(self, df: pd.DataFrame, run_id: str = None,
                                  enrichment_version: str = "v1.0") -> int:
        """
        Write article enrichments (sentiment, entities, etc.) to BigQuery.

        Expected DataFrame columns:
        - Required: url
        - Current: sentiment, sentiment_score
        - Future: issue_labels, entity_labels

        Args:
            df: DataFrame with enrichment data
            run_id: Optional run identifier
            enrichment_version: Version identifier for enrichment pipeline

        Returns:
            Number of rows written
        """
        if df.empty:
            print("⚠️  No enrichments to write")
            return 0

        table_id = self._get_table_ref("article_enrichments")

        # Prepare data
        df = df.copy()
        df['enrichment_timestamp'] = datetime.utcnow()
        df['enrichment_version'] = enrichment_version
        df['run_id'] = run_id

        # Ensure URL column exists
        if 'url' not in df.columns:
            raise ValueError("DataFrame must have 'url' column")

        # Convert list columns to proper format
        if 'issue_labels' in df.columns and df['issue_labels'].dtype == 'object':
            # Ensure it's a list (even if empty)
            df['issue_labels'] = df['issue_labels'].apply(
                lambda x: x if isinstance(x, list) else ([] if pd.isna(x) else [str(x)])
            )

        if 'entity_labels' in df.columns and df['entity_labels'].dtype == 'object':
            df['entity_labels'] = df['entity_labels'].apply(
                lambda x: x if isinstance(x, list) else ([] if pd.isna(x) else [str(x)])
            )

        # Select only columns that exist in schema
        schema_columns = [
            'url', 'sentiment', 'sentiment_score',
            'issue_labels', 'entity_labels', 'custom_metadata',
            'enrichment_timestamp', 'enrichment_version', 'run_id'
        ]
        df_to_write = df[[col for col in schema_columns if col in df.columns]]

        # Write to BigQuery (append mode - allows re-enrichment over time)
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df_to_write, table_id, job_config=job_config)
        job.result()

        print(f"✓ Wrote {len(df_to_write):,} article enrichments to BigQuery: {table_id}")
        return len(df_to_write)

    def get_processed_urls(self, days_back: int = 30) -> set:
        """
        Get URLs of articles collected in the last N days.

        Args:
            days_back: Number of days to look back

        Returns:
            Set of processed URLs
        """
        table_id = self._get_table_ref("collected_articles")

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

    def get_urls_needing_enrichment(self, enrichment_version: str = None) -> List[str]:
        """
        Get URLs that need enrichment (new or different version).

        Args:
            enrichment_version: If specified, only get URLs without this version

        Returns:
            List of URLs needing enrichment
        """
        collected_table = self._get_table_ref("collected_articles")
        enrichments_table = self._get_table_ref("article_enrichments")

        if enrichment_version:
            query = f"""
                SELECT DISTINCT c.url
                FROM `{collected_table}` c
                LEFT JOIN `{enrichments_table}` e
                    ON c.url = e.url
                    AND e.enrichment_version = '{enrichment_version}'
                WHERE e.url IS NULL
                    AND c.article_text IS NOT NULL
            """
        else:
            query = f"""
                SELECT DISTINCT c.url
                FROM `{collected_table}` c
                LEFT JOIN `{enrichments_table}` e ON c.url = e.url
                WHERE e.url IS NULL
                    AND c.article_text IS NOT NULL
            """

        try:
            results = self.client.query(query).result()
            urls = [row.url for row in results]
            print(f"📝 Found {len(urls):,} URLs needing enrichment")
            return urls
        except Exception as e:
            print(f"⚠️  Error getting URLs for enrichment: {e}")
            return []


if __name__ == "__main__":
    # Test the storage module
    storage = BigQueryStorage()
    storage.initialize_tables()

    # Test with sample data
    test_articles = pd.DataFrame({
        'url': ['https://example.com/test1', 'https://example.com/test2'],
        'title': ['Test Article 1', 'Test Article 2'],
        'description': ['Test Description 1', 'Test Description 2'],
        'rank': [1, 2],
        'query': ['test query', 'test query'],
        'article_text': ['Full article text here...', 'Another article text...'],
        'summary': ['Summary 1', 'Summary 2'],
        'keywords': ['test, article', 'test, example'],
        'scraper_used': ['newspaper3k', 'trafilatura']
    })

    storage.write_collected_articles(test_articles, run_id='test_run')

    # Test enrichments
    test_enrichments = pd.DataFrame({
        'url': ['https://example.com/test1', 'https://example.com/test2'],
        'sentiment': ['positive', 'neutral'],
        'sentiment_score': [0.8, 0.1]
    })

    storage.write_article_enrichments(test_enrichments, run_id='test_run')

    print("\n✅ BigQuery storage test complete")
