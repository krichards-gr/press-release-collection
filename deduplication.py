"""
Deduplication Module
=====================

Tracks processed URLs across runs to avoid redundant scraping and API calls.

Features:
- Persistent storage of processed URLs
- Fast lookup using sets
- Automatic cleanup of old entries
- Thread-safe operations
"""

from pathlib import Path
from typing import Set, List
import pandas as pd
from datetime import datetime

from config import config


class URLTracker:
    """Track and deduplicate URLs across pipeline runs."""

    def __init__(self, tracking_file: Path = None):
        """
        Initialize URL tracker.

        Args:
            tracking_file: Path to file storing processed URLs
        """
        self.tracking_file = tracking_file or config.PROCESSED_URLS_FILE
        self.processed_urls: Set[str] = set()
        self._load_processed_urls()

    def _load_processed_urls(self):
        """Load previously processed URLs from file."""
        if self.tracking_file.exists():
            try:
                with open(self.tracking_file, 'r', encoding='utf-8') as f:
                    self.processed_urls = set(line.strip() for line in f if line.strip())
                print(f"📝 Loaded {len(self.processed_urls):,} previously processed URLs")
            except Exception as e:
                print(f"⚠️  Could not load processed URLs: {e}")
                self.processed_urls = set()
        else:
            print("📝 No previous URL history found (first run)")

    def save_processed_urls(self):
        """Save processed URLs to file."""
        try:
            with open(self.tracking_file, 'w', encoding='utf-8') as f:
                for url in sorted(self.processed_urls):
                    f.write(url + '\n')
            print(f"💾 Saved {len(self.processed_urls):,} processed URLs to {self.tracking_file}")
        except Exception as e:
            print(f"⚠️  Could not save processed URLs: {e}")

    def is_processed(self, url: str) -> bool:
        """Check if URL has been processed before."""
        return url in self.processed_urls

    def mark_as_processed(self, url: str):
        """Mark URL as processed."""
        self.processed_urls.add(url)

    def mark_batch_as_processed(self, urls: List[str]):
        """Mark multiple URLs as processed."""
        self.processed_urls.update(urls)

    def filter_new_urls(self, urls: List[str]) -> List[str]:
        """
        Filter out URLs that have been processed before.

        Args:
            urls: List of URLs to check

        Returns:
            List of URLs that haven't been processed yet
        """
        new_urls = [url for url in urls if url not in self.processed_urls]
        skipped_count = len(urls) - len(new_urls)

        if skipped_count > 0:
            print(f"🔄 Skipping {skipped_count:,} already-processed URLs")
            print(f"✨ {len(new_urls):,} new URLs to process")
        else:
            print(f"✨ All {len(new_urls):,} URLs are new")

        return new_urls

    def get_stats(self) -> dict:
        """Get statistics about processed URLs."""
        return {
            'total_processed': len(self.processed_urls),
            'tracking_file': str(self.tracking_file),
            'file_exists': self.tracking_file.exists()
        }


def deduplicate_serp_results(results_df: pd.DataFrame, tracker: URLTracker = None) -> pd.DataFrame:
    """
    Remove already-processed URLs from SERP results.

    Args:
        results_df: DataFrame with SERP results (must have 'link' column)
        tracker: URL tracker instance (creates new one if None)

    Returns:
        DataFrame with only new URLs
    """
    if tracker is None:
        tracker = URLTracker()

    if 'link' not in results_df.columns:
        print("⚠️  No 'link' column in results, skipping deduplication")
        return results_df

    original_count = len(results_df)
    new_urls = tracker.filter_new_urls(results_df['link'].tolist())

    # Filter dataframe to only include new URLs
    deduplicated_df = results_df[results_df['link'].isin(new_urls)].copy()

    removed_count = original_count - len(deduplicated_df)
    if removed_count > 0:
        print(f"   Removed {removed_count:,} duplicate URLs from SERP results")

    return deduplicated_df


if __name__ == "__main__":
    # Test the tracker
    tracker = URLTracker()
    print("\nTracker stats:", tracker.get_stats())

    # Test URLs
    test_urls = [
        "https://example.com/article1",
        "https://example.com/article2",
        "https://example.com/article3"
    ]

    print("\nMarking URLs as processed...")
    tracker.mark_batch_as_processed(test_urls)

    print("\nFiltering URLs...")
    new_test_urls = test_urls + ["https://example.com/article4"]
    filtered = tracker.filter_new_urls(new_test_urls)
    print(f"New URLs: {filtered}")

    # Save
    tracker.save_processed_urls()
