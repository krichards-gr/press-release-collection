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

# Alternative scrapers for fallback chain
import cloudscraper  # Bypasses Cloudflare and other bot protection
import trafilatura  # Robust content extraction
from readability import Document  # Mozilla's readability algorithm
from goose3 import Goose  # Alternative article extractor

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
        self.scraper_counts = Counter()  # Track which scrapers succeeded
        self.failed_urls = []
        self.processing_times = []
        self.start_time = None

    def start(self, total: int):
        """Initialize metrics for a scraping run."""
        with self.lock:
            self.total = total
            self.start_time = time.time()

    def record_success(self, processing_time: float, scraper_used: str = "unknown"):
        """Record a successful scrape."""
        with self.lock:
            self.successful += 1
            self.processing_times.append(processing_time)
            self.scraper_counts[scraper_used] += 1

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

            # Show which scrapers succeeded
            if self.scraper_counts:
                report.append(f"\n🔧 SCRAPER PERFORMANCE:")
                for scraper, count in self.scraper_counts.most_common():
                    percentage = (count / self.successful * 100) if self.successful > 0 else 0
                    report.append(f"   {scraper:.<30} {count:>4} ({percentage:>5.1f}%)")

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
# INDIVIDUAL SCRAPER FUNCTIONS
# =============================================================================
# Each scraper attempts to extract article content using a different library.
# They all return the same standardized format or None on failure.
# This modular design makes it easy to add/remove scrapers from the chain.

def scrape_with_newspaper(url: str, config: Config) -> Optional[Dict]:
    """
    Scraper #1: newspaper3k - Fast general-purpose scraper.

    Pros: Fast, includes NLP for keywords/summary
    Cons: Often blocked by bot protection, struggles with JS-heavy sites
    """
    try:
        article = Article(url, config=config)
        article.download()
        article.parse()
        article.nlp()

        # Validate content
        if not article.text or len(article.text.strip()) < 100:
            return None

        return {
            "title": article.title,
            "url": url,
            "summary": article.summary,
            "publish_date": article.publish_date,
            "keywords": ", ".join(article.keywords) if article.keywords else "",
            "article_text": article.text,
            "scraper_used": "newspaper3k"
        }
    except:
        return None


def scrape_with_trafilatura(url: str) -> Optional[Dict]:
    """
    Scraper #2: trafilatura - Excellent at extracting main content.

    Pros: Very robust, handles many layouts, bypasses some bot protection
    Cons: No automatic keyword/summary generation
    """
    try:
        # Use cloudscraper to bypass bot protection
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=TIMEOUT_SECONDS)

        # Extract content with trafilatura
        text = trafilatura.extract(response.text, include_comments=False)

        if not text or len(text.strip()) < 100:
            return None

        # Extract metadata (title, date)
        metadata = trafilatura.extract_metadata(response.text)

        return {
            "title": metadata.title if metadata and metadata.title else "",
            "url": url,
            "summary": "",  # trafilatura doesn't generate summaries
            "publish_date": metadata.date if metadata and metadata.date else None,
            "keywords": "",  # trafilatura doesn't extract keywords
            "article_text": text,
            "scraper_used": "trafilatura"
        }
    except:
        return None


def scrape_with_readability(url: str) -> Optional[Dict]:
    """
    Scraper #3: readability-lxml - Mozilla's readability algorithm.

    Pros: Good at identifying main content, works on many layouts
    Cons: Returns HTML (needs parsing), no metadata extraction
    """
    try:
        # Use cloudscraper to bypass bot protection
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=TIMEOUT_SECONDS)

        # Apply readability
        doc = Document(response.text)

        # Parse the cleaned HTML to extract text
        soup = BeautifulSoup(doc.summary(), 'html.parser')
        text = soup.get_text(separator='\n', strip=True)

        if not text or len(text.strip()) < 100:
            return None

        return {
            "title": doc.title(),
            "url": url,
            "summary": "",
            "publish_date": None,
            "keywords": "",
            "article_text": text,
            "scraper_used": "readability"
        }
    except:
        return None


def scrape_with_goose(url: str) -> Optional[Dict]:
    """
    Scraper #4: goose3 - Another robust article extractor.

    Pros: Good content extraction, gets metadata
    Cons: Can be slower, occasionally misidentifies content
    """
    try:
        # Initialize goose with config
        with Goose({'browser_user_agent': USER_AGENT}) as g:
            article = g.extract(url=url)

            if not article.cleaned_text or len(article.cleaned_text.strip()) < 100:
                return None

            return {
                "title": article.title,
                "url": url,
                "summary": article.meta_description or "",
                "publish_date": article.publish_date,
                "keywords": ", ".join(article.tags) if article.tags else "",
                "article_text": article.cleaned_text,
                "scraper_used": "goose3"
            }
    except:
        return None


# =============================================================================
# MAIN SCRAPING FUNCTION WITH FALLBACK CHAIN
# =============================================================================

def scrape_single_article(url: str, config: Config, metrics: ScraperMetrics) -> Optional[Dict]:
    """
    Try multiple scrapers in sequence until one succeeds.

    Fallback Chain:
    1. newspaper3k (fast, good NLP)
    2. trafilatura (robust, bypasses bot protection)
    3. readability (Mozilla algorithm)
    4. goose3 (alternative robust option)

    Args:
        url: Article URL to scrape
        config: Newspaper3k configuration object
        metrics: Metrics tracker instance

    Returns:
        Dictionary with article data if successful, None if all scrapers fail
    """
    start_time = time.time()

    # Define the fallback chain - order matters!
    # We try fast scrapers first, then more robust ones
    scrapers = [
        ("newspaper3k", lambda: scrape_with_newspaper(url, config)),
        ("trafilatura", lambda: scrape_with_trafilatura(url)),
        ("readability", lambda: scrape_with_readability(url)),
        ("goose3", lambda: scrape_with_goose(url))
    ]

    # Try each scraper in sequence
    last_error = "All scrapers failed"
    for scraper_name, scraper_func in scrapers:
        try:
            result = scraper_func()

            # Success! Record metrics and return
            if result:
                processing_time = time.time() - start_time

                # Record which scraper succeeded (this helps us understand performance)
                scraper_used = result.get('scraper_used', scraper_name)
                metrics.record_success(processing_time, scraper_used)

                time.sleep(RATE_LIMIT_DELAY)  # Rate limiting

                return result

        except Exception as e:
            # This scraper failed, try the next one
            last_error = f"{scraper_name} failed: {str(e)}"
            continue

    # All scrapers failed - record the failure
    metrics.record_failure(url, "All Scrapers Failed", last_error)
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
