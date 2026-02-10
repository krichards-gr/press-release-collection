from grab_reference_data import grab_reference_data

# Launch search with Bright Data API
from brightdata import BrightDataClient
import os

# Google search
async with BrightDataClient(token='7fbf58e92c2ac4d51db8745aeab0f4c2cf75fdf067e3a1f4aabcdfdc279e735f') as client: 
    results = await client.search.google(query="site:https://corporate.charter.com/newsroom before:2026-02-09 after:2026-02-01", num_results=10)

    # 1. Convert the list of dictionaries directly to a DataFrame
    df = pd.DataFrame(results.data)

    # 2. Optional: Clean up or inspect the data
    print(df.head())

    df.to_csv('final_df.csv')

# def main():
#     print("Hello from press-release-collection!")


if __name__ == "__main__":
    df = grab_reference_data()

