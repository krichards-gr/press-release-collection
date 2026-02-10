"""
Comprehensive Coverage Collector - Article Content Scraper Module
==================================================================

This module extracts full article content from URLs collected by the SERP module.
It enhances the basic SERP data with full text, summaries, keywords, and sentiment.

Workflow:
---------
1. Reads CSV with article URLs from SERP collection
2. Downloads and parses each article using newspaper3k
3. Applies NLP to extract keywords and generate summaries
4. Performs sentiment analysis using spaCy + asent
5. Joins scraped content with original SERP metadata
6. Outputs enriched article data

Input Required:
---------------
- final_df.csv: CSV file with 'link' column containing article URLs
  (typically output from BD_sdk_test.py or manual curation)

Output:
-------
- full_text_jj_articles.csv: Enriched article data with full text and sentiment

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

import subprocess
import sys

# =============================================================================
# DEPENDENCY INSTALLATION (commented out - use pip install -r requirements.txt)
# =============================================================================
# def install_requirements():
#     """Install packages from requirements.txt"""
#     try:
#         subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
#         print("All requirements installed successfully!")
#     except subprocess.CalledProcessError as e:
#         print(f"Error installing requirements: {e}")
#         sys.exit(1)

# Uncomment to auto-install on first run:
# install_requirements()

# =============================================================================
# IMPORTS
# =============================================================================
import requests
from bs4 import BeautifulSoup     # HTML parsing (unused directly but imported by newspaper)
from newspaper import Article      # Article extraction and parsing
import nltk
import time

# Download NLTK punkt tokenizer for sentence splitting
nltk.download('punkt_tab')

from newspaper import Config       # Browser user-agent configuration
import pandas as pd               # Data manipulation
import spacy                      # NLP pipeline
import asent                      # Sentiment analysis for spaCy

# =============================================================================
# CONFIGURATION AND DATA LOADING
# =============================================================================

# Load SERP results CSV containing article URLs to scrape
results_df = pd.read_csv('final_df.csv')
article_urls = results_df["url"].to_list()

# Configure browser user-agent to avoid being blocked by websites
# Some sites return 403 Forbidden without a valid user-agent
user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'
config = Config()
config.browser_user_agent = user_agent

# =============================================================================
# ARTICLE SCRAPING LOOP
# =============================================================================

# Accumulator for scraped article data
df_articles = []

for url in article_urls:
    try:
        # Initialize newspaper Article with browser user-agent config
        article = Article(url, config=config)
        # Download HTML, parse content, and run NLP pipeline
        article.download()   # Fetch article HTML
        article.parse()      # Extract text, title, images, publish date
        article.nlp()        # Generate keywords and summary

        # Extract article metadata and content
        title = article.title
        summary = article.summary
        publish_date = article.publish_date
        keywords = article.keywords
        article_text = article.text
        # Note: newspaper can also extract: authors, top_image, movies, etc.

        df_articles.append({
        "title": title,
        "url": url,
        "summary": summary,
        "publish_date": publish_date,
        "keywords": ", " .join(keywords),
        "article_text": article_text
        })

    except:
        print("Unable to retrieve content at:", url)

    print("**********************************************************")
    print(f"Title: {title}")
    print(f"URL: {url}")
    print("**********************************************************")
    
    time.sleep(0.5)


# Convert list of dictionaries to DataFrame
output_articles = pd.DataFrame(df_articles)

# Remove duplicate articles (same URL scraped multiple times)
output_articles_deduped = output_articles.drop_duplicates(subset=['url'], keep='first')

# Merge scraped content back with original SERP data
# Left join preserves all SERP results, adding scraped content where available
joined = pd.merge(left=results_df, right=output_articles_deduped, how='left', on='url')

# =============================================================================
# SENTIMENT ANALYSIS SETUP
# =============================================================================



# Initialize spaCy with large English model for better accuracy
nlp = spacy.load("en_core_web_lg")
nlp.add_pipe('sentencizer')  # Add sentence boundary detection

# Add asent rule-based sentiment analysis component
# asent uses a lexicon approach similar to VADER
nlp.add_pipe('asent_en_v1')

# Testing
# test_df = pd.read_csv('full_text_jj_articles.csv') # Test data
# test_sample = test_df['article_text'][1]

# doc = nlp(test_sample)
# print(doc._.polarity)

# asent.visualize(doc, style='prediction')


# Apply to article text
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

# =============================================================================
# APPLY SENTIMENT AND SAVE OUTPUT
# =============================================================================

# Apply sentiment analysis to article descriptions
# Note: Using description rather than full text for speed; adjust as needed
joined['sentiment'] = joined['description'].apply(get_sentiment_label)

# Write enriched data to CSV
joined.to_csv("outputs/enriched.csv", index=False)

# =============================================================================
# NEXT STEP: Run results_processing.R for additional enrichment and transformation
# =============================================================================
