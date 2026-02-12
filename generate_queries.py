import pandas as pd
from urllib.parse import quote  # URL encoding for query parameters


# TODO Update the query generation to pull from my pressroom urls

# Function to generate list of queries from two inputs (outlet urls and company name queries)
# def create_search_queries(query_terms, outlet_domains, start_date, end_date):
def create_search_queries(start_date, end_date):
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

    # Load search terms from input CSV
    # TODO: Add interactive selection for different company sets

    # Load query terms (company names/brands to search for)
    # query_terms = pd.read_csv('inputs/company_information.csv')['query'].tolist()

    # Load media outlet domains to search within
    df = pd.read_csv('inputs/reference_data.csv')
    pressroom_urls = df['newsroom_url'].dropna().tolist()  # Drop NaN values

    print(f"📝 Loaded {len(pressroom_urls)} valid newsroom URLs from reference data")

    # for domain in outlet_domains:
    for url in pressroom_urls:
        # Skip empty or invalid URLs
        if not url or not isinstance(url, str) or url.strip() == '':
            print(f"⚠️ Skipping invalid URL: {url}")
            continue

        url = url.strip()  # Remove any whitespace

        # URL-encode the query term to handle special characters
        # safe='' ensures all characters (spaces, &, ", etc.) are encoded
        # encoded_term = quote(term, safe='')

        # Build the complete query
        query = f'https://www.google.com/search?q=site:{url}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1'
        queries.append(query)

    print(f"✅ Generated {len(queries)} search queries")

    return queries

# Usage
# Create universal query element variables -- ADD INTERACTIVE SELECTION
# start_date = '2025-12-29'
# end_date = '2026-01-01'


# # search_queries = create_search_queries(query_terms=query_terms, outlet_domains=outlet_domains, start_date=start_date, end_date=end_date)
# search_queries = create_search_queries(start_date=start_date, end_date=end_date)
