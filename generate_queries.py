import pandas as pd
from urllib.parse import quote  # URL encoding for query parameters

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
    query_terms = pd.read_csv('inputs/company_information.csv')['query'].tolist()

    # Load media outlet domains to search within
    outlet_domains = pd.read_csv('inputs/outlet_w_domain.csv')['domain'].tolist()
    
    for domain in outlet_domains:
        for term in query_terms:
            # URL-encode the query term to handle special characters
            # safe='' ensures all characters (spaces, &, ", etc.) are encoded
            encoded_term = quote(term, safe='')
            
            # Build the complete query
            query = f'https://www.google.com/search?q={encoded_term}+site:{domain}+before:{end_date}+after:{start_date}&gl=US&hl=en&brd_json=1'
            queries.append(query)
    
    return queries
