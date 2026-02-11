# # Launch search with Bright Data API
# from brightdata import BrightDataClient
# import os

# # Google search
# async with BrightDataClient(token='7fbf58e92c2ac4d51db8745aeab0f4c2cf75fdf067e3a1f4aabcdfdc279e735f') as client: 
#     results = await client.search.google(query="site:https://corporate.charter.com/newsroom before:2026-02-09 after:2026-02-01", num_results=10)

#     # 1. Convert the list of dictionaries directly to a DataFrame
#     df = pd.DataFrame(results.data)

#     # 2. Optional: Clean up or inspect the data
#     print(df.head())

#     df.to_csv('final_df.csv')

import json
import pandas as pd
from brightdata import BrightDataClient  # Bright Data SDK for SERP API integration
import requests
from typing import List, Optional


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
        max_pages = 2       # Limit to prevent infinite pagination
        print(f"Processing query: {query}")

        # Paginate through results until no more pages or max reached
        while current_url and page_count < max_pages:
            # Send request through Bright Data's SERP proxy
            # The proxy handles Google search requests and returns structured JSON
            # Credentials format: brd-customer-{customer_id}-zone-{zone_name}:{password}
            response = requests.get(
                current_url,
                proxies={
                    'http': 'http://brd-customer-hl_bb36cd52-zone-corporate_newsroom_collection:n7766z1i0zmm@brd.superproxy.io:33335',
                    'https': 'http://brd-customer-hl_bb36cd52-zone-corporate_newsroom_collection:n7766z1i0zmm@brd.superproxy.io:33335'
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
                    parsed = BrightDataClient.parse_content(response.text)
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
