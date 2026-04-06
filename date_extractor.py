"""
Date Extraction Module
=======================

Extracts publish dates from press release URLs and article text when the
scraper's built-in date extraction fails. Implements a priority chain:

  1. Scraper-extracted publish_date (from newspaper3k, trafilatura, goose3, etc.)
  2. URL-derived date (pattern matching against common newsroom URL formats)
  3. Text-derived date (first date found in article body text)
  4. Collection date (today's date — valid proxy when pipeline runs daily)

Ported from combo.R — covers Fortune 100 newsroom URL date formats.
"""

import re
from datetime import date, datetime
from typing import Optional

import pandas as pd


# =============================================================================
# MONTH HELPERS
# =============================================================================

_MONTH_ABBR_TO_NUM = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_MONTH_FULL_TO_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
    'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
    'november': 11, 'december': 12,
}


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    """Build a date object, returning None if values are out of range."""
    try:
        return date(year, month, day)
    except (ValueError, OverflowError):
        return None


# =============================================================================
# URL DATE EXTRACTION
# =============================================================================

def extract_url_date(url: str) -> Optional[date]:
    """
    Extract a publication date from a press release URL.

    Covers common Fortune 100 newsroom URL patterns:
      /YYYY/MM/DD/   (Walmart, RTX, UnitedHealth, BofA, etc.)
      /YYYY-MM-DD-   (Boeing, Lockheed, AbbVie, Google Cloud, Goldman)
      -YYYY-MM-DD/   (Oracle)
      /MMDDYYYY---   (Publix)
      /YYYY/M/       (Tyson — single-digit month)
      /yYYYY/mMM/    (Cisco)
      /YYYY/mMM/     (variant without y prefix)
      /YYYY/MMDD-    (ExxonMobil)
      /YYYY/mon/MMDD (GM)
      /YYYY/month/   (MetLife)
      monDDYY.pdf    (Berkshire)
      /YYYY/MM/      (Apple, Facebook — month-only, day defaults to 01)

    Returns None if no date pattern is found.
    """
    if not url:
        return None

    url_lower = url.lower()

    # --- /YYYY/MM/DD/ or /YYYY/MM/DD- ---
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})[/-]', url)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # --- /YYYY-MM-DD- (at path start) ---
    m = re.search(r'/(\d{4})-(\d{2})-(\d{2})-', url)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # --- trailing -YYYY-MM-DD/ or -YYYY-MM-DD$ ---
    m = re.search(r'-(\d{4})-(\d{2})-(\d{2})/?$', url)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # --- /MMDDYYYY--- (Publix: 01282026---publix) ---
    m = re.search(r'/(\d{2})(\d{2})(\d{4})---', url)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))

    # --- /YYYY/M/ single-digit month (Tyson: /2026/3/) ---
    m = re.search(r'/(\d{4})/(\d{1,2})/[^\d]', url)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return _safe_date(year, month, 1)

    # --- /yYYYY/mMM/ (Cisco) ---
    m = re.search(r'/y(\d{4})/m(\d{2})/', url_lower)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), 1)

    # --- /YYYY/mMM/ (without y prefix) ---
    m = re.search(r'/(\d{4})/m(\d{2})/', url_lower)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), 1)

    # --- /YYYY/MMDD- (ExxonMobil: /2026/0130-) ---
    m = re.search(r'/(\d{4})/(\d{2})(\d{2})-', url)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # --- /YYYY/mon/MMDD (GM: /2026/jan/0106-) ---
    abbr_pattern = '|'.join(_MONTH_ABBR_TO_NUM.keys())
    m = re.search(
        rf'/(\d{{4}})/({abbr_pattern})/(\d{{2}})(\d{{2}})',
        url_lower
    )
    if m:
        year = int(m.group(1))
        day = int(m.group(4))
        mon = _MONTH_ABBR_TO_NUM.get(m.group(2))
        if mon:
            return _safe_date(year, mon, day)

    # --- /YYYY/month/ spelled out (MetLife: /2026/january/) ---
    full_pattern = '|'.join(_MONTH_FULL_TO_NUM.keys())
    m = re.search(
        rf'/(\d{{4}})/({full_pattern})/',
        url_lower
    )
    if m:
        year = int(m.group(1))
        mon = _MONTH_FULL_TO_NUM.get(m.group(2))
        if mon:
            return _safe_date(year, mon, 1)

    # --- monDDYY.pdf (Berkshire: feb2826.pdf) ---
    m = re.search(
        rf'({abbr_pattern})(\d{{2}})(\d{{2}})\.pdf',
        url_lower
    )
    if m:
        mon = _MONTH_ABBR_TO_NUM.get(m.group(1))
        day = int(m.group(2))
        year = 2000 + int(m.group(3))
        if mon:
            return _safe_date(year, mon, day)

    # --- /YYYY/MM/ month-only (Apple, Facebook: /2026/03/) ---
    m = re.search(r'/(\d{4})/(\d{2})/', url)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return _safe_date(year, month, 1)

    return None


# =============================================================================
# TEXT DATE EXTRACTION
# =============================================================================

# Precompiled patterns for article text date extraction
_MONTH_FULL_RE = (
    r'(?:January|February|March|April|May|June|'
    r'July|August|September|October|November|December)'
)
_MONTH_ABBR_RE = (
    r'(?:Jan\.?|Feb\.?|Mar\.?|Apr\.?|May\.?|Jun\.?|'
    r'Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)'
)

# Pattern 1: "January 7, 2026" or "Feb. 4, 2026"
_PAT_MDY = re.compile(
    rf'({_MONTH_FULL_RE}|{_MONTH_ABBR_RE})\s+(\d{{1,2}}),?\s+(\d{{4}})'
)
# Pattern 2: "12 March 2026" (European style)
_PAT_DMY = re.compile(
    rf'(\d{{1,2}})\s+({_MONTH_FULL_RE}|{_MONTH_ABBR_RE}),?\s+(\d{{4}})'
)
# Pattern 3: MM/DD/YYYY
_PAT_NUMERIC = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})')


def _parse_month_name(name: str) -> Optional[int]:
    """Convert a month name/abbreviation to its number (1-12)."""
    clean = name.rstrip('.').lower()
    # Handle "sept" -> "sep"
    if clean == 'sept':
        clean = 'sep'
    return _MONTH_ABBR_TO_NUM.get(clean) or _MONTH_FULL_TO_NUM.get(clean)


def extract_text_date(text: str) -> Optional[date]:
    """
    Extract the first publication date from press release article text.

    Looks for common date formats in order of prevalence:
      - "January 7, 2026" / "Feb. 4, 2026"  (Month DD, YYYY)
      - "12 March 2026"                       (DD Month YYYY)
      - "01/07/2026"                           (MM/DD/YYYY)

    Returns the first match found, or None.
    """
    if not text:
        return None

    # Try Month DD, YYYY first (most common in US press releases)
    m = _PAT_MDY.search(text)
    if m:
        mon = _parse_month_name(m.group(1))
        if mon:
            return _safe_date(int(m.group(3)), mon, int(m.group(2)))

    # Try DD Month YYYY (European style)
    m = _PAT_DMY.search(text)
    if m:
        mon = _parse_month_name(m.group(2))
        if mon:
            return _safe_date(int(m.group(3)), mon, int(m.group(1)))

    # Try MM/DD/YYYY
    m = _PAT_NUMERIC.search(text)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))

    return None


# =============================================================================
# DATE RESOLUTION (COALESCE CHAIN)
# =============================================================================

def resolve_publish_date(
    scraper_date,
    url: str,
    article_text: str,
    collection_date: Optional[date] = None,
) -> Optional[date]:
    """
    Resolve the best publish date using a priority chain:

      1. scraper_date  — extracted by newspaper3k/trafilatura/goose3
      2. url_date      — parsed from URL path patterns
      3. text_date     — first date found in article body text
      4. collection_date — today's date (proxy when pipeline runs daily)

    Args:
        scraper_date:    Date from the scraper (str, datetime, date, or None).
        url:             Article URL.
        article_text:    Full article text.
        collection_date: Fallback date (defaults to today if not provided).

    Returns:
        A date object, or None if nothing worked (shouldn't happen with
        collection_date fallback).
    """
    # 1. Scraper date
    if scraper_date is not None:
        parsed = _coerce_to_date(scraper_date)
        if parsed is not None:
            return parsed

    # 2. URL date
    url_date = extract_url_date(url)
    if url_date is not None:
        return url_date

    # 3. Text date
    text_date = extract_text_date(article_text)
    if text_date is not None:
        return text_date

    # 4. Collection date fallback
    if collection_date is not None:
        return collection_date
    return date.today()


def _coerce_to_date(val) -> Optional[date]:
    """Convert various date representations to a date object."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    # String
    s = str(val).strip()
    if not s or s.lower() in ('nat', 'none', 'nan', ''):
        return None
    try:
        return pd.to_datetime(s, errors='coerce').date()
    except Exception:
        return None


# =============================================================================
# DATAFRAME HELPER
# =============================================================================

def resolve_dates_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply the date resolution chain to an entire DataFrame.

    Expects columns: 'publish_date', 'url', 'article_text'.
    Adds 'publish_date_source' column indicating which method produced the date.
    Updates 'publish_date' in place with the resolved value.

    Returns the modified DataFrame.
    """
    if df.empty:
        return df

    df = df.copy()
    today = date.today()

    resolved_dates = []
    sources = []

    for _, row in df.iterrows():
        scraper_date = row.get('publish_date')
        url = str(row.get('url', ''))
        text = str(row.get('article_text', '') or '')

        # Check each source in priority order
        parsed_scraper = _coerce_to_date(scraper_date)
        if parsed_scraper is not None:
            resolved_dates.append(parsed_scraper)
            sources.append('scraper')
            continue

        url_date = extract_url_date(url)
        if url_date is not None:
            resolved_dates.append(url_date)
            sources.append('url')
            continue

        text_date = extract_text_date(text)
        if text_date is not None:
            resolved_dates.append(text_date)
            sources.append('text')
            continue

        resolved_dates.append(today)
        sources.append('collection_date')

    df['publish_date'] = resolved_dates
    df['publish_date_source'] = sources
    return df
