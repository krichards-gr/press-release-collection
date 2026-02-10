"""
Comprehensive Coverage Collector - SERP Data Collection Module
===============================================================

This module collects Search Engine Results Page (SERP) data using the Bright Data
SERP API. It searches for specified company/brand terms across a list of media 
outlet domains within a given date range.

Workflow:
---------
1. Reads query terms from CSV (company names, brands, etc.)
2. Reads media outlet domains from CSV
3. Constructs Google search queries with site: and date filters
4. Sends queries through Bright Data's SERP proxy with JSON parsing
5. Handles pagination (up to 20 pages per query)
6. Stores results in DuckDB database and CSV

Inputs Required:
----------------
- inputs/company_information.csv: Contains 'query' column with search terms
- inputs/outlet_w_domain.csv: Contains 'domain' column with media outlet domains

Outputs:
--------
- outputs/kenvue_results.db: DuckDB database with 'raw_kenvue_results' table
- outputs/csvs/kenvue_results.csv: CSV export of raw results

Dependencies:
-------------
- brightdata-sdk: For SERP API integration
- pandas: Data manipulation
- duckdb: Database storage
- requests: HTTP requests through Bright Data proxy

Usage:
------
    python BD_sdk_test.py

Note: Requires valid Bright Data API credentials configured in the bdclient.

Author: KRosh
"""

import subprocess
import sys
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import json
import pandas as pd
from brightdata import bdclient  # Bright Data SDK for SERP API integration
import requests
from typing import List, Optional
from urllib.parse import quote  # URL encoding for query parameters
import duckdb



# =============================================================================
# LOCAL FUNCTIONS
# =============================================================================
# These functions handle query generation and SERP data collection

# Function to automatically install the contents of requirements.txt (all necessary packages)
# def install_requirements():
#     """Install packages from requirements.txt"""
#     try:
#         subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
#         print("All requirements installed successfully!")
#     except subprocess.CalledProcessError as e:
#         print(f"Error installing requirements: {e}")
#         sys.exit(1)


# # Install all required packages
# install_requirements()


# Function to generate list of queries from two inputs (outlet urls and company name queries)
def create_search_queries(query_terms, outlet_domains, start_date, end_date):
    """
    Generate search queries combining individual company terms with domains and date ranges.
    
    Args:
        query_terms: List of company search terms
        outlet_domains: List of domain names
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Returns:
        List of formatted search queries (one per domain-term combination)
    """
    queries = []
    
    for domain in outlet_domains:
        for term in query_terms:
            # URL-encode the query term to handle special characters
            # safe='' ensures all characters (spaces, &, ", etc.) are encoded
            encoded_term = quote(term, safe='')
            
            # Build the complete query
            query = f'https://www.google.com/search?q={encoded_term}+site:{domain}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1'
            queries.append(query)
    
    return queries


def collect_search_results(search_queries: List[str]) -> Optional[pd.DataFrame]:
    """
    Collect search results from multiple queries, with pagination support through up to 20 pages.

    Parameters:
    -----------
    search_queries : List[str]
        List of constructed search query urls to process

    Returns:
    --------
    Optional[pd.DataFrame]
        Combined dataframe with all results, or None if no results are found.
    """

    # Accumulator for all search results across queries and pages
    full_results = []

    # Process each search query
    for query in search_queries:
        current_url = query  # Start with the initial query URL
        page_count = 0       # Track pagination depth
        max_pages = 20       # Limit to prevent infinite pagination
        print(f"Processing query: {query}")

        # Paginate through results until no more pages or max reached
        while current_url and page_count < max_pages:
            # Send request through Bright Data's SERP proxy
            # The proxy handles Google search requests and returns structured JSON
            # Credentials format: brd-customer-{customer_id}-zone-{zone_name}:{password}
            response = requests.get(
                current_url,
                proxies={
                    "http": "http://brd-customer-c_bb36cd52-zone-jj_coverage_monitor:y506tdfo2nlq@brd.superproxy.io:33335",
                    "https": "http://brd-customer-c_bb36cd52-zone-jj_coverage_monitor:y506tdfo2nlq@brd.superproxy.io:33335",
                },
                verify=False,  # SSL verification disabled for proxy
            )
            # Try to parse JSON response directly
            try:
                parsed = json.loads(response.text)
            except json.JSONDecodeError:
                # Fallback: Use Bright Data SDK parser if direct JSON parsing fails
                # This handles cases where response format is non-standard
                print("Invalid JSON response, attempting SDK parse")
                try:
                    parsed = client.parse_content(response.text)
                    print(type(parsed))
                    print("SDK parse successful, continuing...")
                except Exception as e:
                    print(f"SDK parse failed: {e}")
                    current_url = None
                    break

            # Check if organic (non-ad) results exist; exit if none
            if not parsed.get("organic"):
                current_url = None
                break

            # Extract key fields from organic search results
            # Fields: title, description, link, rank (position on page)
            # Extract key fields from organic search results
            # Fields: title, description, link, rank (position on page)
            df = pd.DataFrame(parsed["organic"])
            required_columns = ["title", "description", "link", "rank"]
            for col in required_columns:
                if col not in df.columns:
                    df[col] = None  # Fill missing columns with None
            
            data = df[required_columns]
            # Add the query string for later company name matching
            data["query"] = parsed["general"]["query"]
            full_results.append(data)

            # Check for next page link -- default None
            try:
                pagination = parsed.get("pagination", {})
                next_page_link = (
                    pagination.get("next_page_link") if pagination else None
                )
                current_url = next_page_link + "&brd_json=1" if next_page_link else None

            except (KeyError, IndexError):
                current_url = None

            page_count += 1
            print(f"Page {page_count}: Next URL: {current_url}")

    # Combine all results into final dataframe
    if full_results:
        final_df = pd.concat(full_results, ignore_index=True)
        print(f"{len(final_df)} results collected!")
        return final_df
    else:
        print("No Results Returned")
        return None


# =============================================================================
# CONFIGURATION AND INPUT LOADING
# =============================================================================



# Load search terms from input CSV
# TODO: Add interactive selection for different company sets

# Load query terms (company names/brands to search for)
query_terms = pd.read_csv('inputs/company_information.csv')['query'].tolist()

# Load media outlet domains to search within
outlet_domains = pd.read_csv('inputs/outlet_w_domain.csv')['domain'].tolist()

# Create universal query element variables -- ADD INTERACTIVE SELECTION
start_date = '2025-12-29'
end_date = '2026-01-01'

# Generate list of queries to send to SERP API
search_queries = create_search_queries(query_terms=query_terms, outlet_domains=outlet_domains, start_date=start_date, end_date=end_date)

# Filter just XYZ queries --ADMIN
# search_queries = [query for query in search_queries if "apnews" in query or "axios" in query]
# test_queries = search_queries[1:15]


# Initiate Bright Data SERP API client (API Key expires 4/3/2026)
client = bdclient(api_token='7fbf58e92c2ac4d51db8745aeab0f4c2cf75fdf067e3a1f4aabcdfdc279e735f',
serp_zone='jj_coverage_monitor')

###--------------------------------------------------------------------------------------------###



# =============================================================================
# MAIN EXECUTION: SEARCH DATA COLLECTION
# =============================================================================
results = collect_search_results(search_queries=search_queries)

# =============================================================================
# OUTPUT: PERSIST RESULTS TO STORAGE
# =============================================================================



###-------------------------------------  Write to disk   -------------------------------------###

# Write to duckdb database
conn = duckdb.connect('outputs/Q4_results.db') # Open connection (or create database if one doesn't exist)

# Create table if not exists (w/ primary key [on what??])
conn.sql('CREATE TABLE IF NOT EXISTS raw_serp_results (title VARCHAR, description VARCHAR, link VARCHAR, rank INTEGER, query VARCHAR, PRIMARY KEY (query, link))')

# Copy results to table
conn.sql('INSERT OR IGNORE INTO raw_serp_results SELECT * FROM results')

conn.close() # Close connection

# Write to csv
results.to_csv('outputs/csvs/raw_serp_results.csv', index=False) # Write dataframe of results to disk

# -- Data is now ready for article_scraper.py or results_processing.R --



# =============================================================================
# NEXT STEP: Run results_processing.R for enrichment and transformation
# =============================================================================

