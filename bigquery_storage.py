"""
BigQuery Storage Module
========================

Manages all BigQuery table operations for the press release collection pipeline.
Replaces CSV storage with scalable cloud data warehouse.

Tables:
- serp_results: Raw search engine results
- scraped_articles: Full article content
- enriched_articles: Final data with sentiment analysis
"""

import os
from datetime import datetime
from typing import Optional
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
            dataset_id: BigQuery dataset ID (default: press_release_collection)
        """
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id or self.client.project
        self.dataset_id = dataset_id or os.getenv('BIGQUERY_DATASET', 'press_release_collection')
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

    def create_serp_results_table(self):
        """Create table for SERP results if it doesn't exist."""
        table_id = self._get_table_ref("serp_results")

        schema = [
            bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("description", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("link", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("rank", "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("query", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("collection_date", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE"),
        ]

        table = bigquery.Table(table_id, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_date"
        )

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_scraped_articles_table(self):
        """Create table for scraped article content."""
        table_id = self._get_table_ref("scraped_articles")

        schema = [
            bigquery.SchemaField("url", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("summary", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("article_text", "STRING", mode="NULLABLE"),
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

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def create_enriched_articles_table(self):
        """Create table for enriched articles with sentiment."""
        table_id = self._get_table_ref("enriched_articles")

        schema = [
            bigquery.SchemaField("url", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("description", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("article_text", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("summary", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("keywords", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("publish_date", "TIMESTAMP", mode="NULLABLE"),
            bigquery.SchemaField("sentiment", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("scraper_used", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("run_id", "STRING", mode="NULLABLE"),
        ]

        table = bigquery.Table(table_id, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="collection_timestamp"
        )

        try:
            self.client.get_table(table_id)
            print(f"✓ Table exists: {table_id}")
        except NotFound:
            self.client.create_table(table)
            print(f"✓ Created table: {table_id}")

    def initialize_tables(self):
        """Create all required tables."""
        print("\n📊 Initializing BigQuery tables...")
        self.create_serp_results_table()
        self.create_scraped_articles_table()
        self.create_enriched_articles_table()
        print()

    def write_serp_results(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write SERP results to BigQuery.

        Args:
            df: DataFrame with SERP results
            run_id: Optional run identifier

        Returns:
            Number of rows written
        """
        if df.empty:
            print("⚠️  No SERP results to write")
            return 0

        table_id = self._get_table_ref("serp_results")

        # Add metadata columns
        df = df.copy()
        df['collection_date'] = datetime.utcnow()
        df['run_id'] = run_id

        # Ensure schema compatibility
        if 'link' not in df.columns:
            raise ValueError("DataFrame must have 'link' column")

        # Write to BigQuery
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()  # Wait for completion

        print(f"✓ Wrote {len(df):,} SERP results to BigQuery: {table_id}")
        return len(df)

    def write_scraped_articles(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write scraped articles to BigQuery.

        Args:
            df: DataFrame with article content
            run_id: Optional run identifier

        Returns:
            Number of rows written
        """
        if df.empty:
            print("⚠️  No articles to write")
            return 0

        table_id = self._get_table_ref("scraped_articles")

        # Add metadata columns
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Ensure schema compatibility
        if 'url' not in df.columns:
            raise ValueError("DataFrame must have 'url' column")

        # Convert publish_date to datetime if it exists
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Write to BigQuery
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()

        print(f"✓ Wrote {len(df):,} articles to BigQuery: {table_id}")
        return len(df)

    def write_enriched_articles(self, df: pd.DataFrame, run_id: str = None) -> int:
        """
        Write enriched articles to BigQuery.

        Args:
            df: DataFrame with enriched content
            run_id: Optional run identifier

        Returns:
            Number of rows written
        """
        if df.empty:
            print("⚠️  No enriched articles to write")
            return 0

        table_id = self._get_table_ref("enriched_articles")

        # Add metadata columns
        df = df.copy()
        df['collection_timestamp'] = datetime.utcnow()
        df['run_id'] = run_id

        # Ensure schema compatibility
        if 'url' not in df.columns:
            raise ValueError("DataFrame must have 'url' column")

        # Convert publish_date to datetime if it exists
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')

        # Write to BigQuery
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()

        print(f"✓ Wrote {len(df):,} enriched articles to BigQuery: {table_id}")
        return len(df)

    def get_processed_urls(self, days_back: int = 30) -> set:
        """
        Get URLs processed in the last N days.

        Args:
            days_back: Number of days to look back

        Returns:
            Set of processed URLs
        """
        table_id = self._get_table_ref("scraped_articles")

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


if __name__ == "__main__":
    # Test the storage module
    storage = BigQueryStorage()
    storage.initialize_tables()

    # Test with sample data
    test_serp = pd.DataFrame({
        'title': ['Test Article'],
        'description': ['Test Description'],
        'link': ['https://example.com/test'],
        'rank': [1],
        'query': ['test query']
    })

    storage.write_serp_results(test_serp, run_id='test_run')
    print("\n✅ BigQuery storage test complete")
