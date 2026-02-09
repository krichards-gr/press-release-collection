from google.cloud import bigquery
import pandas

client = bigquery.Client()

# Perform a query.
QUERY = (
    'SELECT * FROM `sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference` '
        )

query_job = client.query(QUERY)  # API request
rows = query_job.result()  # Waits for query to finish

df = rows.to_dataframe() # Convert query results to pandas dataframe

filtered_df = df[df['F100'] & df['newsroom_url'].notna()]['newsroom_url'] # Filter dataframe & select newsroom base url column



for row in rows:
    print(row.corporation)


# def main():
#     print("Hello from press-release-collection!")


if __name__ == "__main__":
    main()
