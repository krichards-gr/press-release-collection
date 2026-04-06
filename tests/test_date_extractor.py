"""Tests for date_extractor.py — URL and text date extraction + resolution chain."""

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from date_extractor import (
    extract_url_date,
    extract_text_date,
    resolve_publish_date,
    resolve_dates_df,
)


# =========================================================================
# URL DATE EXTRACTION
# =========================================================================

class TestExtractUrlDate:
    """Test extract_url_date against Fortune 100 newsroom URL patterns."""

    def test_yyyy_mm_dd_slash(self):
        # Walmart, RTX, UnitedHealth, BofA
        url = "https://corporate.walmart.com/news/2026/03/15/some-press-release"
        assert extract_url_date(url) == date(2026, 3, 15)

    def test_yyyy_mm_dd_dash_prefix(self):
        # Boeing, Lockheed, AbbVie, Google Cloud, Goldman
        url = "https://news.boeing.com/2026-01-22-boeing-announces-something"
        assert extract_url_date(url) == date(2026, 1, 22)

    def test_trailing_yyyy_mm_dd(self):
        # Oracle
        url = "https://www.oracle.com/news/announcement/something-2026-02-10/"
        assert extract_url_date(url) == date(2026, 2, 10)

    def test_trailing_yyyy_mm_dd_no_slash(self):
        url = "https://www.oracle.com/news/announcement/something-2026-02-10"
        assert extract_url_date(url) == date(2026, 2, 10)

    def test_mmddyyyy_triple_dash(self):
        # Publix: 01282026---publix
        url = "https://newsroom.publix.com/01282026---publix-announces-thing"
        assert extract_url_date(url) == date(2026, 1, 28)

    def test_single_digit_month(self):
        # Tyson: /2026/3/
        url = "https://ir.tyson.com/news/2026/3/article-slug"
        assert extract_url_date(url) == date(2026, 3, 1)

    def test_cisco_y_m_prefix(self):
        # Cisco: /y2026/m02/
        url = "https://newsroom.cisco.com/press-release/y2026/m02/something"
        assert extract_url_date(url) == date(2026, 2, 1)

    def test_yyyy_m_prefix_no_y(self):
        url = "https://example.com/news/2026/m01/article"
        assert extract_url_date(url) == date(2026, 1, 1)

    def test_exxonmobil_yyyy_mmdd(self):
        # ExxonMobil: /2026/0130-
        url = "https://corporate.exxonmobil.com/news/2026/0130-exxon-press"
        assert extract_url_date(url) == date(2026, 1, 30)

    def test_gm_year_mon_mmdd(self):
        # GM: /2026/jan/0106-
        url = "https://news.gm.com/newsroom/2026/jan/0106-gm-announces"
        assert extract_url_date(url) == date(2026, 1, 6)

    def test_metlife_spelled_month(self):
        # MetLife: /2026/january/
        url = "https://www.metlife.com/press/2026/january/something"
        assert extract_url_date(url) == date(2026, 1, 1)

    def test_berkshire_pdf(self):
        # Berkshire: feb2826.pdf
        url = "https://www.berkshirehathaway.com/news/feb2826.pdf"
        assert extract_url_date(url) == date(2026, 2, 28)

    def test_yyyy_mm_month_only(self):
        # Apple, Facebook: /2026/03/
        url = "https://newsroom.apple.com/2026/03/"
        assert extract_url_date(url) == date(2026, 3, 1)

    def test_no_date_in_url(self):
        url = "https://www.example.com/press-releases/big-announcement"
        assert extract_url_date(url) is None

    def test_empty_url(self):
        assert extract_url_date("") is None
        assert extract_url_date(None) is None


# =========================================================================
# TEXT DATE EXTRACTION
# =========================================================================

class TestExtractTextDate:
    """Test extract_text_date against common press release date formats."""

    def test_full_month_day_year(self):
        text = "BENTONVILLE, Ark., January 7, 2026 — Walmart today announced..."
        assert extract_text_date(text) == date(2026, 1, 7)

    def test_abbreviated_month(self):
        text = "NEW YORK, Feb. 4, 2026 — Company reports quarterly earnings..."
        assert extract_text_date(text) == date(2026, 2, 4)

    def test_abbreviated_no_period(self):
        text = "CHICAGO, Mar 12, 2026 - Press release content here."
        assert extract_text_date(text) == date(2026, 3, 12)

    def test_european_style(self):
        text = "London, 15 March 2026 — Announcement text follows."
        assert extract_text_date(text) == date(2026, 3, 15)

    def test_numeric_slash(self):
        text = "Report date: 01/15/2026. Full details below."
        assert extract_text_date(text) == date(2026, 1, 15)

    def test_sept_abbreviation(self):
        text = "Published Sept. 5, 2026"
        assert extract_text_date(text) == date(2026, 9, 5)

    def test_no_date_in_text(self):
        text = "This press release has no dates in it whatsoever."
        assert extract_text_date(text) is None

    def test_empty_text(self):
        assert extract_text_date("") is None
        assert extract_text_date(None) is None


# =========================================================================
# RESOLVE PUBLISH DATE (COALESCE CHAIN)
# =========================================================================

class TestResolvePublishDate:
    """Test the priority chain: scraper → URL → text → collection_date."""

    def test_scraper_date_wins(self):
        result = resolve_publish_date(
            scraper_date="2026-01-15",
            url="https://example.com/news/2026/02/01/article",
            article_text="March 1, 2026 — Some text",
            collection_date=date(2026, 3, 19),
        )
        assert result == date(2026, 1, 15)

    def test_url_date_fallback(self):
        result = resolve_publish_date(
            scraper_date=None,
            url="https://example.com/news/2026/02/01/article",
            article_text="March 1, 2026 — Some text",
            collection_date=date(2026, 3, 19),
        )
        assert result == date(2026, 2, 1)

    def test_text_date_fallback(self):
        result = resolve_publish_date(
            scraper_date=None,
            url="https://example.com/press/article-slug",
            article_text="March 1, 2026 — Some text",
            collection_date=date(2026, 3, 19),
        )
        assert result == date(2026, 3, 1)

    def test_collection_date_fallback(self):
        result = resolve_publish_date(
            scraper_date=None,
            url="https://example.com/press/article-slug",
            article_text="No dates here at all.",
            collection_date=date(2026, 3, 19),
        )
        assert result == date(2026, 3, 19)

    def test_defaults_to_today(self):
        result = resolve_publish_date(
            scraper_date=None,
            url="https://example.com/press/article-slug",
            article_text="No dates here.",
        )
        assert result == date.today()

    def test_scraper_datetime_object(self):
        result = resolve_publish_date(
            scraper_date=datetime(2026, 1, 15, 10, 30),
            url="",
            article_text="",
        )
        assert result == date(2026, 1, 15)

    def test_scraper_nan_falls_through(self):
        result = resolve_publish_date(
            scraper_date=float('nan'),
            url="https://example.com/news/2026/02/01/article",
            article_text="",
        )
        assert result == date(2026, 2, 1)


# =========================================================================
# DATAFRAME HELPER
# =========================================================================

class TestResolveDatesDf:
    """Test resolve_dates_df on a DataFrame."""

    def test_mixed_sources(self):
        df = pd.DataFrame({
            'url': [
                'https://example.com/news/2026/03/15/article',
                'https://example.com/press/no-date-slug',
                'https://example.com/press/no-date-slug',
            ],
            'article_text': [
                'Some text',
                'January 20, 2026 — Announcement',
                'No dates here.',
            ],
            'publish_date': [
                '2026-01-01',
                None,
                None,
            ],
        })

        result = resolve_dates_df(df)

        assert result.loc[0, 'publish_date'] == date(2026, 1, 1)
        assert result.loc[0, 'publish_date_source'] == 'scraper'

        assert result.loc[1, 'publish_date'] == date(2026, 1, 20)
        assert result.loc[1, 'publish_date_source'] == 'text'

        assert result.loc[2, 'publish_date'] == date.today()
        assert result.loc[2, 'publish_date_source'] == 'collection_date'

    def test_empty_df(self):
        df = pd.DataFrame(columns=['url', 'article_text', 'publish_date'])
        result = resolve_dates_df(df)
        assert result.empty
