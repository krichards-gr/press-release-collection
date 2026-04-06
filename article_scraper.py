"""
Article Content Scraper Module
================================

Extracts full article text from URLs collected by the SERP module.
This is a COLLECTION-ONLY module -- no analysis, no summaries, no keywords.

How it works:
  1. Reads the CSV of article URLs produced by collect_results.py
  2. Filters out non-article URLs (pagination pages, home pages, archives)
  3. For each valid URL, tries a chain of 5 scrapers in order until one succeeds
  4. Joins scraped content back with original SERP metadata
  5. Writes the joined CSV that downstream code (or BigQuery) consumes

Scraper fallback chain (tried in order, fastest first):
  1. newspaper3k   -- fast general-purpose scraper
  2. trafilatura    -- robust content extraction with cloudscraper for bot bypass
  3. readability    -- Mozilla's readability algorithm (extracts main content from HTML)
  4. goose3         -- alternative robust article extractor
  5. Bright Data Unlocker -- premium paid API, only if BRIGHT_DATA_UNLOCKER_API_KEY is set

Each scraper returns a standardized dict or None on failure. The chain stops
at the first success, so most articles are scraped by newspaper3k (cheapest/fastest).

Concurrency:
  Uses ThreadPoolExecutor with SCRAPER_MAX_WORKERS threads (default 10).
  Each URL is independently scraped in its own thread with a per-request timeout.

Input:
  outputs/f100_collected_results.csv  (from collect_results.py)

Output:
  outputs/f100_joined.csv            (SERP metadata + scraped article_text)
  outputs/scraper_errors.csv         (detailed failure log for debugging)
  outputs/filtered_urls.csv          (URLs skipped by is_valid_article_url)

Usage:
  python article_scraper.py
"""

# =============================================================================
# IMPORTS
# =============================================================================
import os
import re
import requests
from bs4 import BeautifulSoup
from newspaper import Article, Config as NewspaperConfig, ArticleException
import nltk
import time
import sys
from datetime import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Dict, List, Optional

# newspaper3k needs the NLTK punkt tokenizer for sentence splitting.
# Download it silently on import so the scraper works out-of-the-box.
nltk.download('punkt_tab', quiet=True)

import pandas as pd
from tqdm import tqdm

# Alternative scrapers used in the fallback chain:
import cloudscraper   # Bypasses Cloudflare and similar bot protection
import trafilatura    # Robust main-content extraction from HTML
from readability import Document   # Mozilla's readability algorithm
from goose3 import Goose           # Alternative article extractor


# =============================================================================
# CONFIGURATION
# =============================================================================
# These can also be set via environment variables (see config.py), but are
# hardcoded here as module-level constants for the standalone scraper.

MAX_WORKERS = 10          # Number of concurrent scraping threads
TIMEOUT_SECONDS = 30      # Per-article HTTP request timeout
RETRY_ATTEMPTS = 2        # Retries per scraper on transient failure
RATE_LIMIT_DELAY = 0.1    # Seconds between requests (politeness delay)

# Minimum characters of extracted text to consider a scrape successful.
# 400 chars filters out JS-rendered page shells (~327 chars) that trafilatura
# sometimes returns as "content", which would stop the chain too early.
# Note: newspaper3k (first in chain) uses its own 100-char threshold, so
# short but legitimate articles are still captured.
MIN_CONTENT_LENGTH = 400

# Browser User-Agent string to avoid being blocked by corporate newsrooms.
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/50.0.2661.102 Safari/537.36'
)


# =============================================================================
# METRICS TRACKING
# =============================================================================
# Thread-safe class that accumulates statistics during a scraping run.
# Used to generate the execution report printed at the end.

class ScraperMetrics:
    """
    Thread-safe metrics tracker for scraping operations.

    Multiple scraper threads call record_success / record_failure concurrently,
    so every mutation is protected by a Lock.
    """

    def __init__(self):
        self.lock = Lock()           # Guards all mutable state below
        self.total = 0               # Total URLs found in the input CSV
        self.filtered = 0            # URLs skipped by is_valid_article_url()
        self.successful = 0          # URLs where a scraper returned content
        self.failed = 0              # URLs where ALL scrapers failed
        self.error_counts = Counter()      # error_type -> count
        self.scraper_counts = Counter()    # scraper_name -> success count
        self.failed_urls = []              # List of {url, error_type, error_message, timestamp}
        self.processing_times = []         # Seconds taken per successful scrape
        self.start_time = None             # Wall-clock start of the run

    def start(self, total: int):
        """Initialize metrics at the beginning of a scraping run."""
        with self.lock:
            self.total = total
            self.start_time = time.time()

    def record_filtered(self):
        """Record that a URL was filtered out (not an article)."""
        with self.lock:
            self.filtered += 1

    def record_success(self, processing_time: float, scraper_used: str = "unknown"):
        """Record a successful scrape, noting which scraper worked."""
        with self.lock:
            self.successful += 1
            self.processing_times.append(processing_time)
            self.scraper_counts[scraper_used] += 1

    def record_failure(self, url: str, error_type: str, error_message: str):
        """Record a failed scrape with details for the error log."""
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
        """Return current success/fail counts for the progress bar."""
        with self.lock:
            return {
                'success': self.successful,
                'failed': self.failed,
                'total': self.total
            }

    def generate_report(self) -> str:
        """
        Generate a human-readable execution report.

        Called once at the end of the run. Shows overall stats, scraper
        performance breakdown, and error breakdown.
        """
        with self.lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            success_rate = (self.successful / self.total * 100) if self.total > 0 else 0
            avg_time = (
                sum(self.processing_times) / len(self.processing_times)
                if self.processing_times else 0
            )
            attempted = self.total - self.filtered  # URLs actually sent to scrapers

            report = [
                "\n" + "=" * 80,
                "ARTICLE SCRAPER EXECUTION REPORT",
                "=" * 80,
                f"\nOVERALL STATISTICS:",
                f"   Total URLs Found:         {self.total:,}",
            ]

            # Show filtered URLs if any were removed
            if self.filtered > 0:
                filter_pct = (self.filtered / self.total * 100) if self.total > 0 else 0
                report.append(f"   Filtered (non-articles):  {self.filtered:,} ({filter_pct:.1f}%)")
                report.append(f"   URLs Attempted:           {attempted:,}")

            report.extend([
                f"   Successful:               {self.successful:,} ({success_rate:.1f}%)",
                f"   Failed:                   {self.failed:,} ({100 - success_rate:.1f}%)",
                f"\nPERFORMANCE METRICS:",
                f"   Total Execution Time:     {elapsed:.2f}s ({elapsed / 60:.1f}m)",
                f"   Average Time per Article: {avg_time:.2f}s",
                f"   Throughput:               {attempted / elapsed:.2f} articles/sec"
                if elapsed > 0 else "   Throughput:               N/A",
            ])

            # Which scrapers did the heavy lifting?
            if self.scraper_counts:
                report.append(f"\nSCRAPER PERFORMANCE:")
                for scraper, count in self.scraper_counts.most_common():
                    pct = (count / self.successful * 100) if self.successful > 0 else 0
                    report.append(f"   {scraper:.<30} {count:>4} ({pct:>5.1f}%)")

            # What went wrong?
            if self.error_counts:
                report.append(f"\nERROR BREAKDOWN:")
                for error_type, count in self.error_counts.most_common():
                    pct = (count / self.failed * 100) if self.failed > 0 else 0
                    report.append(f"   {error_type:.<30} {count:>4} ({pct:>5.1f}%)")

            if self.failed > 0:
                report.append(f"\nERROR LOG:")
                report.append(f"   Detailed error log saved to: outputs/scraper_errors.csv")

            report.append("\n" + "=" * 80 + "\n")
            return "\n".join(report)

    def save_error_log(self, filepath: str):
        """Save detailed error log to CSV for later analysis / retry."""
        if self.failed_urls:
            error_df = pd.DataFrame(self.failed_urls)
            error_df.to_csv(filepath, index=False)
            return True
        return False


# =============================================================================
# URL VALIDATION
# =============================================================================
# Filters out URLs that are clearly NOT individual articles. These are things
# like pagination pages, category listings, home pages, and search results
# that sometimes appear in SERP results alongside real articles.

def is_valid_article_url(url: str) -> bool:
    """
    Check if a URL looks like an actual article (not a listing/index/home page).

    Returns True if the URL appears to be an individual article.
    Returns False if it matches any of our "not an article" patterns.

    Patterns we filter out:
      - Pagination:  ?page=3, &page=2, /page/5
      - Home pages:  /press, /newsroom, /news (with nothing after)
      - Categories:  /category/, /tag/, /topic/
      - Archives:    /archive/, /2026/ (year-only)
      - Search:      ?search=, ?q=
    """
    url_lower = url.lower()

    # -- Pagination URLs --
    if any(p in url_lower for p in ['?page=', '&page=', '/page/']):
        return False

    # -- Home/index pages (path ends with a directory name, no article slug) --
    path_ends = ['/press', '/newsroom', '/news', '/press-releases',
                 '/media', '/press-release']
    if any(url_lower.rstrip('/').endswith(ending) for ending in path_ends):
        return False

    # -- Category, tag, archive, author pages --
    if any(p in url_lower for p in ['/category/', '/tag/', '/topic/',
                                     '/archive/', '/author/']):
        return False

    # -- Search results (but allow section IDs like ?s=2429&item=123) --
    if any(p in url_lower for p in ['?search=', '?q=', '&search=', '&q=']):
        return False

    # Special handling for ?s= parameter: only filter if the value looks like
    # a search term (words), not a numeric section/category ID.
    if '?s=' in url_lower and '&item=' not in url_lower:
        s_param = re.search(r'\?s=([^&]+)', url_lower)
        if s_param and not s_param.group(1).isdigit():
            return False

    # -- Year-only archives (e.g. /2026/) but allow /2026/03/article-slug --
    if re.search(r'/\d{4}/?$', url_lower):
        return False

    # Passed all filters -- looks like an article!
    return True


# =============================================================================
# INDIVIDUAL SCRAPER FUNCTIONS
# =============================================================================
# Each function attempts to extract article content using a different library.
# They all return the same standardized dict on success, or None on failure.
# This modular design makes it easy to add, remove, or reorder scrapers.
#
# Return dict format:
#   {
#       "url":           str   -- the original URL
#       "scraped_title": str   -- page title extracted by the scraper
#       "article_text":  str   -- full article body text
#       "publish_date":  str|None -- publication date if the scraper found one
#       "scraper_used":  str   -- name of the scraper that succeeded
#   }

def scrape_with_newspaper(url: str, config: NewspaperConfig) -> Optional[Dict]:
    """
    Scraper #1: newspaper3k -- fast general-purpose article scraper.

    Pros: Fast, extracts title + publish_date automatically.
    Cons: Often blocked by bot protection, struggles with JS-heavy sites.

    Note: We call article.parse() but NOT article.nlp() since we don't
    need summaries or keywords (collection-only pipeline).
    """
    try:
        article = Article(url, config=config)
        article.download()
        article.parse()
        # Skip article.nlp() -- we don't need summaries/keywords

        # Reject if content is too short (likely a failed extraction)
        if not article.text or len(article.text.strip()) < 100:
            return None

        return {
            "url": url,
            "scraped_title": article.title or "",
            "article_text": article.text,
            "publish_date": article.publish_date,
            "scraper_used": "newspaper3k"
        }
    except Exception:
        return None


def scrape_with_trafilatura(url: str) -> Optional[Dict]:
    """
    Scraper #2: trafilatura -- excellent at extracting main article content.

    Pros: Very robust, handles many page layouts. Uses cloudscraper under
          the hood here to bypass Cloudflare-style bot protection.
    Cons: No automatic publish_date extraction from all sites.
    """
    try:
        # Use cloudscraper to get past bot protection before feeding HTML
        # to trafilatura for content extraction.
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()  # Reject 4xx/5xx error pages

        # Extract the main article text from the raw HTML
        text = trafilatura.extract(response.text, include_comments=False)

        if not text or len(text.strip()) < MIN_CONTENT_LENGTH:
            return None

        # Also try to extract metadata (title, date) from the HTML
        metadata = trafilatura.extract_metadata(response.text)

        return {
            "url": url,
            "scraped_title": (metadata.title if metadata and metadata.title else "") or "",
            "article_text": text,
            "publish_date": metadata.date if metadata and metadata.date else None,
            "scraper_used": "trafilatura"
        }
    except Exception:
        return None


def scrape_with_readability(url: str) -> Optional[Dict]:
    """
    Scraper #3: readability-lxml -- Mozilla's readability algorithm.

    Pros: Great at finding the "main content" area of a page.
    Cons: Returns HTML (needs BeautifulSoup parsing), no date extraction.
    """
    try:
        # Use cloudscraper to bypass bot protection
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()

        # Apply Mozilla's readability algorithm to isolate main content
        doc = Document(response.text)

        # readability returns cleaned HTML, so we parse it to plain text
        soup = BeautifulSoup(doc.summary(), 'html.parser')
        text = soup.get_text(separator='\n', strip=True)

        if not text or len(text.strip()) < MIN_CONTENT_LENGTH:
            return None

        return {
            "url": url,
            "scraped_title": doc.title() or "",
            "article_text": text,
            "publish_date": None,  # readability doesn't extract dates
            "scraper_used": "readability"
        }
    except Exception:
        return None


def scrape_with_goose(url: str) -> Optional[Dict]:
    """
    Scraper #4: goose3 -- alternative robust article extractor.

    Pros: Good content extraction, often gets publish_date.
    Cons: Can be slower, occasionally misidentifies content boundaries.
    """
    try:
        with Goose({'browser_user_agent': USER_AGENT}) as g:
            article = g.extract(url=url)

            if not article.cleaned_text or len(article.cleaned_text.strip()) < 100:
                return None

            return {
                "url": url,
                "scraped_title": article.title or "",
                "article_text": article.cleaned_text,
                "publish_date": article.publish_date,
                "scraper_used": "goose3"
            }
    except Exception:
        return None


def scrape_with_bright_data_unlocker(url: str) -> Optional[Dict]:
    """
    Scraper #5: Bright Data Unlocker API -- premium paid fallback.

    This is the LAST resort for sites with advanced bot protection
    (Cloudflare, Akamai, Imperva, etc.) that defeat all free scrapers.
    Only invoked when all free options fail, since it costs money per request.

    Requires: BRIGHT_DATA_UNLOCKER_API_KEY environment variable.
    If the key is not set, this scraper silently returns None (skipped).
    """
    api_key = os.getenv('BRIGHT_DATA_UNLOCKER_API_KEY', '').strip()
    if not api_key:
        # No API key configured -- skip this scraper entirely
        return None

    try:
        # Use Bright Data's Unlocker API to fetch the page with JS rendering
        response = requests.post(
            "https://api.brightdata.com/request",
            json={
                "zone": "corporate_newsroom_unlocker",
                "url": url,
                "format": "raw",
                "method": "GET",
                "direct": True
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()

        html = response.text
        if not html:
            return None

        # Feed the rendered HTML to trafilatura for content extraction
        text = trafilatura.extract(html, include_comments=False)
        if not text or len(text.strip()) < 100:
            return None

        metadata = trafilatura.extract_metadata(html)

        return {
            "url": url,
            "scraped_title": (metadata.title if metadata and metadata.title else "") or "",
            "article_text": text,
            "publish_date": metadata.date if metadata and metadata.date else None,
            "scraper_used": "bright_data_unlocker"
        }
    except Exception:
        return None


# =============================================================================
# MAIN SCRAPING FUNCTION WITH FALLBACK CHAIN
# =============================================================================

def scrape_single_article(url: str, config: NewspaperConfig,
                          metrics: ScraperMetrics) -> Optional[Dict]:
    """
    Try multiple scrapers in sequence until one succeeds.

    The fallback chain is ordered from fastest/cheapest to slowest/most expensive:
      1. newspaper3k    (fast, free)
      2. trafilatura    (robust, free, uses cloudscraper)
      3. readability    (Mozilla algorithm, free)
      4. goose3         (alternative, free)
      5. bright_data    (paid API, only if API key is set)

    Args:
        url:     Article URL to scrape.
        config:  newspaper3k Config object (user-agent, timeout).
        metrics: Thread-safe metrics tracker for recording success/failure.

    Returns:
        Dict with article data if any scraper succeeds, None if all fail.
    """
    start_time = time.time()

    # Define the fallback chain -- ORDER MATTERS!
    # Fast/free scrapers first, expensive ones last.
    scrapers = [
        ("newspaper3k",          lambda: scrape_with_newspaper(url, config)),
        ("trafilatura",          lambda: scrape_with_trafilatura(url)),
        ("readability",          lambda: scrape_with_readability(url)),
        ("goose3",               lambda: scrape_with_goose(url)),
        ("bright_data_unlocker", lambda: scrape_with_bright_data_unlocker(url)),
    ]

    # Try each scraper in sequence; stop at the first success
    last_error = "All scrapers failed"
    for scraper_name, scraper_func in scrapers:
        try:
            result = scraper_func()

            if result:
                # This scraper succeeded -- record metrics and return
                processing_time = time.time() - start_time
                metrics.record_success(processing_time, result.get('scraper_used', scraper_name))
                time.sleep(RATE_LIMIT_DELAY)  # Politeness delay
                return result

        except Exception as e:
            # This scraper threw an exception -- try the next one
            last_error = f"{scraper_name} failed: {str(e)}"
            continue

    # All scrapers failed for this URL
    metrics.record_failure(url, "All Scrapers Failed", last_error)
    return None


def scrape_articles_concurrent(urls: List[str], max_workers: int = MAX_WORKERS,
                               total_urls: int = None,
                               filtered_urls: int = 0) -> List[Dict]:
    """
    Scrape multiple articles concurrently using a thread pool.

    Args:
        urls:           List of article URLs to scrape.
        max_workers:    Maximum number of concurrent threads.
        total_urls:     Total URLs before filtering (for accurate metrics).
        filtered_urls:  Number of URLs filtered out (for accurate metrics).

    Returns:
        List of dicts, one per successfully scraped article.
    """
    # Initialize metrics tracker
    metrics = ScraperMetrics()
    metrics.total = total_urls if total_urls else len(urls)
    metrics.filtered = filtered_urls
    metrics.start_time = time.time()

    # Configure newspaper3k's settings (used by scraper #1)
    newspaper_config = NewspaperConfig()
    newspaper_config.browser_user_agent = USER_AGENT
    newspaper_config.request_timeout = TIMEOUT_SECONDS

    # Thread-safe list for collecting results from worker threads
    articles = []
    articles_lock = Lock()

    # Progress bar so the user can see scraping progress in real time
    pbar = tqdm(
        total=len(urls),
        desc="Scraping Articles",
        unit="article",
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
    )

    # Launch all scraping tasks into a thread pool
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit one task per URL
        future_to_url = {
            executor.submit(scrape_single_article, url, newspaper_config, metrics): url
            for url in urls
        }

        # Process results as they complete (not necessarily in submission order)
        for future in as_completed(future_to_url):
            result = future.result()
            if result:
                with articles_lock:
                    articles.append(result)

            # Update progress bar with live success/fail stats
            stats = metrics.get_progress_stats()
            pbar.set_postfix(
                success=stats['success'],
                failed=stats['failed'],
                rate=(
                    f"{stats['success'] / (stats['success'] + stats['failed']) * 100:.1f}%"
                    if (stats['success'] + stats['failed']) > 0 else "0%"
                )
            )
            pbar.update(1)

    pbar.close()

    # Print the execution report (scraper performance breakdown, errors, etc.)
    print(metrics.generate_report())

    # Save detailed error log if there were failures
    if metrics.failed > 0:
        from config import config as pipeline_config
        metrics.save_error_log(str(pipeline_config.SCRAPER_ERRORS_FILE))

    return articles


# =============================================================================
# MAIN EXECUTION
# =============================================================================
# When run as a standalone script (python article_scraper.py), this reads
# the SERP results CSV, filters URLs, scrapes concurrently, and writes
# the joined output CSV.

if __name__ == "__main__":
    print("\nStarting Article Scraper...")
    print(f"Configuration: {MAX_WORKERS} workers, {TIMEOUT_SECONDS}s timeout, "
          f"{RETRY_ATTEMPTS} retries\n")

    # -------------------------------------------------------------------------
    # Load SERP results CSV (produced by collect_results.py)
    # -------------------------------------------------------------------------
    print("Loading SERP results...")
    from config import config
    results_df = pd.read_csv(config.COLLECTED_RESULTS_FILE)

    # Normalize column name: SERP module uses 'link', we use 'url' everywhere
    results_df = results_df.rename(columns={"link": "url"})
    all_urls = results_df["url"].to_list()
    print(f"   Found {len(all_urls):,} URLs from SERP results")

    # -------------------------------------------------------------------------
    # Filter out non-article URLs (pagination, home pages, archives, etc.)
    # -------------------------------------------------------------------------
    print("\nFiltering URLs...")
    article_urls = []
    filtered_urls_list = []

    for url in all_urls:
        if is_valid_article_url(url):
            article_urls.append(url)
        else:
            filtered_urls_list.append(url)

    filtered_count = len(filtered_urls_list)

    if filtered_count > 0:
        print(f"   Filtered out {filtered_count:,} non-article URLs "
              f"(pagination, home pages, etc.)")
        print(f"   {len(article_urls):,} valid article URLs to scrape")

        # Save filtered URLs to CSV so the user can review what was skipped
        filtered_df = pd.DataFrame({
            'url': filtered_urls_list,
            'reason': 'Non-article URL (pagination/index/home page)'
        })
        filtered_df.to_csv(config.FILTERED_URLS_FILE, index=False)
        print(f"   Filtered URLs saved to {config.FILTERED_URLS_FILE}\n")
    else:
        print(f"   All {len(article_urls):,} URLs appear to be articles\n")

    # -------------------------------------------------------------------------
    # Scrape articles concurrently using the fallback chain
    # -------------------------------------------------------------------------
    scraped_articles = scrape_articles_concurrent(
        article_urls, total_urls=len(all_urls), filtered_urls=filtered_count
    )

    # -------------------------------------------------------------------------
    # Process results: deduplicate, merge with SERP data, replace titles
    # -------------------------------------------------------------------------
    print("\nProcessing results...")
    output_articles = pd.DataFrame(scraped_articles)

    if not output_articles.empty:
        # Remove duplicate URLs (same URL might appear in multiple SERP queries)
        output_articles_deduped = output_articles.drop_duplicates(
            subset=['url'], keep='first'
        )
        dupes = len(output_articles) - len(output_articles_deduped)
        if dupes:
            print(f"   Removed {dupes} duplicate entries")

        # Merge scraped content back with original SERP metadata (left join
        # so we keep all SERP rows even if scraping failed for that URL).
        joined = pd.merge(
            left=results_df, right=output_articles_deduped,
            how='left', on='url'
        )

        # Replace truncated SERP titles with full scraped page titles.
        # SERP titles are often cut off with "..." -- the scraped title is better.
        if 'scraped_title' in joined.columns:
            has_scraped = (
                joined['scraped_title'].notna()
                & (joined['scraped_title'].str.strip() != '')
            )
            joined.loc[has_scraped, 'title'] = joined.loc[has_scraped, 'scraped_title']
            joined = joined.drop(columns=['scraped_title'])

        # Resolve publish_date using priority chain:
        # scraper date → URL date → text date → collection date (today)
        from date_extractor import resolve_dates_df
        before_count = joined['publish_date'].notna().sum() if 'publish_date' in joined.columns else 0
        joined = resolve_dates_df(joined)
        after_count = joined['publish_date'].notna().sum()
        filled = after_count - before_count
        if filled > 0:
            print(f"   Date resolution filled {filled:,} additional publish_date values")
        if 'publish_date_source' in joined.columns:
            source_counts = joined['publish_date_source'].value_counts()
            print(f"   Date sources: {dict(source_counts)}")

        # Write the final joined CSV
        joined.to_csv(config.JOINED_RESULTS_FILE, index=False)
        print(f"   Saved joined data to {config.JOINED_RESULTS_FILE}")
    else:
        print("   No articles successfully scraped!")

    print("\nArticle scraping complete!")
    print("=" * 80 + "\n")
