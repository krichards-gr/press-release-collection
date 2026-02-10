from google.cloud import bigquery
import pandas as pd

client = bigquery.Client()

# Perform a query.
QUERY = (
    'SELECT corporation, sector, newsroom_url '
    ' FROM `sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference` '
    ' WHERE F100 IS TRUE '
    ' AND newsroom_url IS NOT NULL'
        )

def grab_reference_data():
    query_job = client.query(QUERY)  # API request
    rows = query_job.result()  # Waits for query to finish
    df = rows.to_dataframe() # Convert query results to pandas dataframe
    return df

# Usage
# df = grab_reference_data()