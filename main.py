from google.cloud import bigquery
import pandas as pd

client = bigquery.Client()

# Perform a query.
QUERY = (
    'SELECT * FROM `sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference` '
        )

query_job = client.query(QUERY)  # API request
rows = query_job.result()  # Waits for query to finish

df = rows.to_dataframe() # Convert query results to pandas dataframe

filtered_df = df[df['F100'] & df['newsroom_url'].notna()]['newsroom_url'] # Filter dataframe & select newsroom base url column



# Launch search with Bright Data API
from brightdata import BrightDataClient
import os

client = BrightDataClient()

# Google search
async with BrightDataClient() as client: 
    results = await client.search.google(query="site:https://corporate.charter.com/newsroom before:2026-02-09 after:2026-02-01", num_results=10)

    # 1. Convert the list of dictionaries directly to a DataFrame
    df = pd.DataFrame(results.data)

    # 2. Optional: Clean up or inspect the data
    print(df.head())

# def main():
#     print("Hello from press-release-collection!")


if __name__ == "__main__":
    main()
