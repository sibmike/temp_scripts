"""
Fetch the FCC BDC Fixed Broadband Availability file list (all states, all techs)
and write it to fcc_fixed_broadband.csv.

No FCC API token required -- uses the same public endpoint the data-download web UI calls.
The output CSV has one row per state x technology, with a direct download_link for each.

Run from this directory:
    /c/Users/mikea/anaconda3/python.exe get_fcc_file_list.py
"""
import requests
import csv

# =============================================================================
# STEP 1: GET THE PROCESSING ID (UUID) FOR THE CURRENT DATA RELEASE
# =============================================================================
# This UUID identifies the FCC data release version and changes every ~6 months.
# You need to update it whenever the FCC publishes new data.
#
# HOW TO FIND IT -- pick any of these methods:
#
# OPTION A -- Browser Address Bar (easiest):
#   Go to: https://broadbandmap.fcc.gov/data-download/nationwide-data
#   The URL will show: ?version=jun2025&pubDataVer=jun2025
#   That tells you the release name. Then use Option B or C to get the UUID.
#
# OPTION B -- Chrome DevTools Network Tab:
#   1. Open the FCC data download page
#   2. Press F12 -> click the "Network" tab
#   3. Press Ctrl+R to reload
#   4. In the filter box, type: nbm_get_data_download
#   5. The UUID is in the request URL:
#      /nbm/map/api/national_map_process/nbm_get_data_download/{UUID}/
#
# OPTION C -- Chrome DevTools Network Tab (alternative filter):
#   Same steps as above but filter by: map_processing_updates
#   The very first request on page load will be:
#      GET /api/reference/map_processing_updates/{UUID}
#
# OPTION D -- Hardcode after finding it (fine for one-off scripts):
#   Just paste the UUID directly as shown below.
# =============================================================================

PROCESSING_ID = "987851a7-3c62-416f-8bdd-9058e9ca762f"  # Jun 2025 release

# Step 2: Fetch the full file list
url = f"https://broadbandmap.fcc.gov/nbm/map/api/national_map_process/nbm_get_data_download/{PROCESSING_ID}/"
resp = requests.get(url)
data = resp.json()["data"]

# Step 3: Filter for Fixed Broadband, state-level (Nationwide) only
fixed = [
    d for d in data
    if d["data_type"] == "Fixed Broadband"
    and d["data_category"] == "Nationwide"
    and d["download_available"] == "Yes"
]

# Step 4: Write CSV
base_url = "https://broadbandmap.fcc.gov/nbm/map/api/getNBMDataDownloadFile"
with open("fcc_fixed_broadband.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["state_name", "state_fips", "technology", "file_id", "file_name", "download_link"])
    writer.writeheader()
    for d in fixed:
        writer.writerow({
            "state_name": d["state_name"],
            "state_fips": d["state_fips"],
            "technology": d["technology_code_desc"],
            "file_id": d["id"],
            "file_name": d["file_name"],
            "download_link": f"{base_url}/{d['id']}/1"
        })

print(f"Wrote {len(fixed)} rows")
