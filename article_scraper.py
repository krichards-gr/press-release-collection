"""
Comprehensive Coverage Collector - Article Content Scraper Module
==================================================================

This module extracts full article content from URLs collected by the SERP module.
It enhances the basic SERP data with full text, summaries, keywords, and sentiment.

Workflow:
---------
1. Reads CSV with article URLs from SERP collection
2. Downloads and parses each article using newspaper3k (with concurrent processing)
3. Applies NLP to extract keywords and generate summaries
4. Performs sentiment analysis using spaCy + asent
5. Joins scraped content with original SERP metadata
6. Outputs enriched article data and detailed execution report

Input Required:
---------------
- f100_collected_results.csv: CSV file with 'link' column containing article URLs
  (output from collect_results.py)

Output:
-------
- f100_joined.csv: Joined SERP data with scraped content
- enriched.csv: Final enriched data with sentiment analysis
- scraper_errors.csv: Detailed error log for failed URLs

Dependencies:
-------------
- newspaper3k: Article extraction and NLP
- spacy: NLP pipeline (requires en_core_web_lg model)
- asent: Rule-based sentiment analysis for spaCy
- nltk: Natural language toolkit (punkt tokenizer)
- beautifulsoup4: HTML parsing (used by newspaper)

Usage:
------
    python article_scraper.py

Note: First run may require downloading NLTK punkt tokenizer.

Author: KRosh
"""

# =============================================================================
# IMPORTS
# =============================================================================
import requests
from bs4 import BeautifulSoup
from newspaper import Article, Config, ArticleException
import nltk
import time
import sys
from datetime import datetime
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Dict, List, Optional, Tuple

# Download NLTK punkt tokenizer for sentence splitting
nltk.download('punkt_tab', quiet=True)

import pandas as pd
from tqdm import tqdm
import spacy
import asent

# =============================================================================
# CONFIGURATION
# =============================================================================

# Scraper configuration
MAX_WORKERS = 10  # Number of concurrent threads for scraping
TIMEOUT_SECONDS = 30  # Timeout for article download
RETRY_ATTEMPTS = 2  # Number of retries for transient failures
RATE_LIMIT_DELAY = 0.1  # Delay between requests (seconds) to avoid overwhelming servers

# Configure browser user-agent to avoid being blocked by websites
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'


# =============================================================================
# METRICS TRACKING CLASS
# =============================================================================

class ScraperMetrics:
    """Thread-safe metrics tracker for scraping operations."""

    def __init__(self):
        self.lock = Lock()
        self.total = 0
        self.successful = 0
        self.failed = 0
        self.error_counts = Counter()
        self.failed_urls = []
        self.processing_times = []
        self.start_time = None

    def start(self, total: int):
        """Initialize metrics for a scraping run."""
        with self.lock:
            self.total = total
            self.start_time = time.time()

    def record_success(self, processing_time: float):
        """Record a successful scrape."""
        with self.lock:
            self.successful += 1
            self.processing_times.append(processing_time)

    def record_failure(self, url: str, error_type: str, error_message: str):
        """Record a failed scrape with details."""
        with self.lock:
            self.failed += 1
            self.error_counts[error_type] += 1
            self.failed_urls.append({
                'url': url,
                'error_type': error_type,
                'error_message': error_message,
                'timestamp': datetime.now().isoformat()
            })

    def get_progress_stats(self) -> Dict[str, int]:
        """Get current progress statistics."""
        with self.lock:
            return {
                'success': self.successful,
                'failed': self.failed,
                'total': self.total
            }

    def generate_report(self) -> str:
        """Generate a comprehensive execution report."""
        with self.lock:
            elapsed_time = time.time() - self.start_time if self.start_time else 0
            success_rate = (self.successful / self.total * 100) if self.total > 0 else 0
            avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0

            report = [
                "\n" + "="*80,
                "ARTICLE SCRAPER EXECUTION REPORT",
                "="*80,
                f"\n📊 OVERALL STATISTICS:",
                f"   Total URLs Processed:     {self.total:,}",
                f"   ✓ Successful:             {self.successful:,} ({success_rate:.1f}%)",
                f"   ✗ Failed:                 {self.failed:,} ({100-success_rate:.1f}%)",
                f"\n⏱  PERFORMANCE METRICS:",
                f"   Total Execution Time:     {elapsed_time:.2f}s ({elapsed_time/60:.1f}m)",
                f"   Average Time per Article: {avg_time:.2f}s",
                f"   Throughput:               {self.total/elapsed_time:.2f} articles/sec",
            ]

            if self.error_counts:
                report.append(f"\n❌ ERROR BREAKDOWN:")
                for error_type, count in self.error_counts.most_common():
                    percentage = (count / self.failed * 100) if self.failed > 0 else 0
                    report.append(f"   {error_type:.<30} {count:>4} ({percentage:>5.1f}%)")

            if self.failed > 0:
                report.append(f"\n📝 ERROR LOG:")
                report.append(f"   Detailed error log saved to: outputs/scraper_errors.csv")
                report.append(f"   Failed URLs can be retried using the error log")

            report.append("\n" + "="*80 + "\n")

            return "\n".join(report)

    def save_error_log(self, filepath: str):
        """Save detailed error log to CSV."""
        if self.failed_urls:
            error_df = pd.DataFrame(self.failed_urls)
            error_df.to_csv(filepath, index=False)
            return True
        return False

# =============================================================================
# ARTICLE SCRAPING FUNCTIONS
# =============================================================================

def scrape_single_article(url: str, config: Config, metrics: ScraperMetrics) -> Optional[Dict]:
    """
    Scrape a single article with comprehensive error handling and retry logic.

    Args:
        url: Article URL to scrape
        config: Newspaper3k configuration object
        metrics: Metrics tracker instance

    Returns:
        Dictionary with article data if successful, None if failed
    """
    start_time = time.time()

    for attempt in range(RETRY_ATTEMPTS):
        try:
            # Initialize newspaper Article with browser user-agent config
            article = Article(url, config=config)

            # Download HTML (with timeout handling)
            article.download()

            # Parse content
            article.parse()

            # Run NLP pipeline (keywords and summary extraction)
            article.nlp()

            # Validate that we got meaningful content
            if not article.text or len(article.text.strip()) < 100:
                raise ValueError("Article text too short or empty")

            # Record success
            processing_time = time.time() - start_time
            metrics.record_success(processing_time)

            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)

            # Return structured data
            return {
                "title": article.title,
                "url": url,
                "summary": article.summary,
                "publish_date": article.publish_date,
                "keywords": ", ".join(article.keywords) if article.keywords else "",
                "article_text": article.text
            }

        except ArticleException as e:
            error_type = "Article Parsing Error"
            error_msg = str(e)
            # Don't retry ArticleException - usually means bad URL or paywall
            break

        except requests.exceptions.Timeout:
            error_type = "Timeout Error"
            error_msg = f"Request timed out after {TIMEOUT_SECONDS}s"
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(1 * (attempt + 1))  # Exponential backoff
                continue
            break

        except requests.exceptions.ConnectionError as e:
            error_type = "Connection Error"
            error_msg = str(e)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(1 * (attempt + 1))
                continue
            break

        except requests.exceptions.HTTPError as e:
            error_type = "HTTP Error"
            error_msg = f"HTTP {e.response.status_code}" if hasattr(e, 'response') else str(e)
            # Don't retry 4xx errors
            if hasattr(e, 'response') and 400 <= e.response.status_code < 500:
                break
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(1 * (attempt + 1))
                continue
            break

        except ValueError as e:
            error_type = "Validation Error"
            error_msg = str(e)
            break

        except Exception as e:
            error_type = "Unknown Error"
            error_msg = f"{type(e).__name__}: {str(e)}"
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(1 * (attempt + 1))
                continue
            break

    # If we get here, all retries failed
    metrics.record_failure(url, error_type, error_msg)
    return None


def scrape_articles_concurrent(urls: List[str], max_workers: int = MAX_WORKERS) -> List[Dict]:
    """
    Scrape multiple articles concurrently with progress tracking.

    Args:
        urls: List of article URLs to scrape
        max_workers: Maximum number of concurrent threads

    Returns:
        List of dictionaries containing scraped article data
    """
    # Initialize metrics tracker
    metrics = ScraperMetrics()
    metrics.start(len(urls))

    # Configure newspaper
    config = Config()
    config.browser_user_agent = USER_AGENT
    config.request_timeout = TIMEOUT_SECONDS

    # Storage for successful scrapes
    articles = []
    articles_lock = Lock()

    # Progress bar with custom formatting
    pbar = tqdm(
        total=len(urls),
        desc="Scraping Articles",
        unit="article",
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
    )

    # Concurrent execution
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_url = {
            executor.submit(scrape_single_article, url, config, metrics): url
            for url in urls
        }

        # Process completed tasks
        for future in as_completed(future_to_url):
            result = future.result()
            if result:
                with articles_lock:
                    articles.append(result)

            # Update progress bar with live stats
            stats = metrics.get_progress_stats()
            pbar.set_postfix(
                success=stats['success'],
                failed=stats['failed'],
                rate=f"{stats['success']/(stats['success']+stats['failed'])*100:.1f}%" if (stats['success']+stats['failed']) > 0 else "0%"
            )
            pbar.update(1)

    pbar.close()

    # Generate and display report
    print(metrics.generate_report())

    # Save error log if there were failures
    if metrics.failed > 0:
        metrics.save_error_log('outputs/scraper_errors.csv')

    return articles


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Starting Article Scraper...")
    print(f"Configuration: {MAX_WORKERS} workers, {TIMEOUT_SECONDS}s timeout, {RETRY_ATTEMPTS} retries\n")

    # Load SERP results CSV containing article URLs to scrape
    print("📂 Loading SERP results...")
    results_df = pd.read_csv('outputs/f100_collected_results.csv')
    results_df = results_df.rename(columns={"link": "url"})
    article_urls = results_df["url"].to_list()
    print(f"   Found {len(article_urls):,} URLs to process\n")

    # Scrape articles concurrently
    scraped_articles = scrape_articles_concurrent(article_urls)

    # Convert to DataFrame and remove duplicates
    print("\n📊 Processing results...")
    output_articles = pd.DataFrame(scraped_articles)

    if not output_articles.empty:
        output_articles_deduped = output_articles.drop_duplicates(subset=['url'], keep='first')
        print(f"   Removed {len(output_articles) - len(output_articles_deduped)} duplicate entries")

        # Merge scraped content back with original SERP data
        joined = pd.merge(left=results_df, right=output_articles_deduped, how='left', on='url')
        joined.to_csv("outputs/f100_joined.csv", index=False)
        print(f"   ✓ Saved joined data to outputs/f100_joined.csv")
    else:
        print("   ⚠ No articles successfully scraped!")
        joined = results_df
    # =============================================================================
    # SENTIMENT ANALYSIS
    # =============================================================================

    print("\n🔍 Running sentiment analysis...")

    # Initialize spaCy with large English model for better accuracy
    nlp = spacy.load("en_core_web_lg")
    nlp.add_pipe('sentencizer')  # Add sentence boundary detection

    # Add asent rule-based sentiment analysis component
    # asent uses a lexicon approach similar to VADER
    nlp.add_pipe('asent_en_v1')

    def get_sentiment_label(text):
        """
        Analyze text sentiment using spaCy + asent and return a categorical label.

        The polarity score from asent ranges from -1 (most negative) to +1 (most positive).
        We use thresholds of ±0.1 to classify text as positive/negative/neutral.

        Args:
            text: String content to analyze (typically article description or full text)

        Returns:
            str: 'positive', 'negative', or 'neutral'
        """
        if pd.isna(text) or text == '':
            return 'neutral'

        try:
            doc = nlp(str(text))
            polarity = doc._.polarity

            # Classification thresholds (adjust based on validation results)
            if polarity > 0.1:
                return 'positive'
            elif polarity < -0.1:
                return 'negative'
            else:
                return 'neutral'
        except:
            return 'neutral'  # Default to neutral if processing fails

    # Apply sentiment analysis to article descriptions
    # Note: Using description rather than full text for speed
    tqdm.pandas(desc="Analyzing Sentiment")
    joined['sentiment'] = joined['description'].progress_apply(get_sentiment_label)

    # Write enriched data to CSV
    joined.to_csv("outputs/enriched.csv", index=False)
    print(f"   ✓ Saved enriched data to outputs/enriched.csv")

    print("\n✅ Article scraping complete!")
    print("="*80 + "\n")
