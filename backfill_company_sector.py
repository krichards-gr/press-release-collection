"""
One-time backfill script: populate company, sector, newsroom_url on
ALL rows in press_release_metadata (replacing existing values AND filling blanks).

Matches each row's `query` column against the benchmarking_corporate_reference
table, with reference rows sorted by Rank ascending so the highest-ranked
(lowest rank number) company wins when multiple could match.

Runs one UPDATE per unique newsroom URL to avoid MERGE "multiple source rows"
errors caused by duplicate press_release_ids in the metadata table.
"""

import re
import time
import urllib.parse
from google.cloud import bigquery

client = bigquery.Client()
project = client.project
dataset = "pressure_monitoring"
meta_table = f"{project}.{dataset}.press_release_metadata"
ref_table = "sri-benchmarking-databases.social_media_activity_archive.benchmarking_corporate_reference"

# ── Step 1: Load reference data (sorted by Rank ASC = highest priority first) ─
print("Loading reference data (sorted by Rank ascending)...")
ref_q = f"""
    SELECT corporation, sector, RTRIM(newsroom_url, '/') AS newsroom_url, Rank
    FROM `{ref_table}`
    WHERE Rank <= 100 AND newsroom_url IS NOT NULL
    ORDER BY Rank ASC
"""
ref_rows = list(client.query(ref_q).result())
# Keep rank order — first match in this list wins (lowest rank = highest priority)
ref_list = [(r.newsroom_url, r.corporation, r.sector, r.Rank) for r in ref_rows]
print(f"  Loaded {len(ref_list)} reference entries (rank {ref_list[0][3]} to {ref_list[-1][3]})")


# ── Step 2: Load ALL rows (not just empty ones) ──────────────────────────────
print("Loading ALL metadata rows...")
rows_q = f"""
    SELECT press_release_id, query
    FROM `{meta_table}`
    WHERE query IS NOT NULL AND query != ''
"""
rows = list(client.query(rows_q).result())
print(f"  Found {len(rows)} rows to process")
if not rows:
    print("Nothing to do.")
    exit(0)


# ── Step 3: Match each row to a reference company ───────────────────────────
def extract_newsroom(query_text):
    """Parse the newsroom URL from a SERP query string.

    Handles two formats:
      1. Full Google URL: https://www.google.com/search?q=site:https://...
      2. Raw query text:  site:https://about.att.com/story/ before:2026-03-17
    """
    # Try as full URL first
    try:
        parsed = urllib.parse.urlparse(query_text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            q_param = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            m = re.match(r"site:(\S+)", q_param)
            if m:
                return m.group(1).rstrip("/")
    except Exception:
        pass
    # Try as raw query text
    m = re.match(r"site:(\S+)", query_text.strip())
    if m:
        return m.group(1).rstrip("/")
    return ""


updates = []  # list of (press_release_id, company, sector, newsroom_url)
no_match = []

for row in rows:
    newsroom = extract_newsroom(row.query)
    if not newsroom:
        continue

    # Find first match by rank order (ref_list is already sorted by Rank ASC).
    # The first reference URL that is a prefix of the extracted newsroom wins.
    company, sector = "", ""
    for ref_url, ref_corp, ref_sector, _rank in ref_list:
        if newsroom.startswith(ref_url):
            company, sector = ref_corp, ref_sector
            break

    if company:
        updates.append((row.press_release_id, company, sector, newsroom))
    else:
        no_match.append(newsroom)

print(f"  Matched: {len(updates)}, No match: {len(no_match)}")
if no_match:
    unique_no_match = sorted(set(no_match))
    for nm in unique_no_match[:10]:
        print(f"    No match: {nm}")
    if len(unique_no_match) > 10:
        print(f"    ... and {len(unique_no_match) - 10} more")


# ── Step 4: Batch update by newsroom URL ─────────────────────────────────────
# Group updates by newsroom → (company, sector). One UPDATE per newsroom
# touches ALL rows whose query contains that newsroom URL (no company IS NULL
# filter — we overwrite existing values too).
if not updates:
    print("No updates to apply.")
    exit(0)

# Build unique (newsroom -> company, sector) mapping
newsroom_updates = {}
for pid, company, sector, newsroom in updates:
    newsroom_updates[newsroom] = (company, sector)

print(f"Running {len(newsroom_updates)} batch UPDATE statements...")
total_updated = 0
max_retries = 3

for i, (newsroom, (company, sector)) in enumerate(newsroom_updates.items()):
    # No WHERE company IS NULL filter — update ALL matching rows
    update_q = (
        f"UPDATE `{meta_table}` "
        f"SET company = @company, sector = @sector, newsroom_url = @newsroom "
        f"WHERE query LIKE @pattern"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("company", "STRING", company),
            bigquery.ScalarQueryParameter("sector", "STRING", sector),
            bigquery.ScalarQueryParameter("newsroom", "STRING", newsroom),
            bigquery.ScalarQueryParameter("pattern", "STRING", f"%site:{newsroom}%"),
        ]
    )

    # Retry on concurrent update errors
    for attempt in range(max_retries):
        try:
            result = client.query(update_q, job_config=job_config).result()
            n = result.num_dml_affected_rows or 0
            total_updated += n
            break
        except Exception as e:
            if "concurrent" in str(e).lower() and attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  Concurrent update error on {newsroom}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ERROR updating {newsroom}: {e}")
                break

    if (i + 1) % 20 == 0:
        print(f"  {i + 1}/{len(newsroom_updates)} done ({total_updated} rows so far)")

print(f"  Done: {total_updated} rows updated across {len(newsroom_updates)} companies")

# ── Step 5: Verify ──────────────────────────────────────────────────────────
verify_q = f"""
SELECT
    COUNTIF(company IS NOT NULL AND company != '') AS filled,
    COUNTIF(company IS NULL OR company = '') AS missing,
    COUNT(*) AS total
FROM `{meta_table}`
"""
for row in client.query(verify_q).result():
    print(f"\nFinal state: {row.filled} filled, {row.missing} missing, {row.total} total")
