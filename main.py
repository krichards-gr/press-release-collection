# Import required libraries/modules
import pandas as pd

# Import functions from helper scripts
from grab_reference_data import grab_reference_data
from generate_queries import create_search_queries

# Create universal query element variables -- ADD INTERACTIVE SELECTION
start_date = '2025-12-29'
end_date = '2026-01-01'


if __name__ == "__main__":
    df = grab_reference_data()
    df.head()
    # search_queries = create_search_queries(query_terms=query_terms, outlet_domains=outlet_domains, start_date=start_date, end_date=end_date)
    search_queries = create_search_queries(start_date=start_date, end_date=end_date)
    print(search_queries[0])

