"""
One-off: download FCC BDC Jun 2025 Fixed Broadband Availability data
(all technologies, all states/territories), unzip into data/fcc_bdc_jun2025/<state>/.

Uses the same public endpoints the broadbandmap.fcc.gov data-download UI calls.
No FCC API token / registration required.

Usage:
    python scripts/download_fcc_bdc.py --list-only          # dry run, prints file list
    python scripts/download_fcc_bdc.py --only DC            # one state pilot
    python scripts/download_fcc_bdc.py                      # full run (~1-3 GB)
    python scripts/download_fcc_bdc.py --workers 4          # gentler parallelism
    python scripts/download_fcc_bdc.py --keep-zips          # keep raw zips after extract
"""
import argparse
import http.cookiejar
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HOST = "https://broadbandmap.fcc.gov"
UA = "Mozilla/5.0 (telegrapher_ai bdc-fetch)"
OUT = Path("data/fcc_bdc_jun2025")
TARGET_FILING = "jun 2025"  # case-insensitive substring match against filing label

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_opener.addheaders = [
    ("User-Agent", UA),
    ("Accept", "application/json, text/plain, */*"),
    ("Accept-Language", "en-US,en;q=0.9"),
    ("Referer", f"{HOST}/data-download/nationwide-data?version=jun2025&pubDataVer=jun2025"),
]


def warm_session():
    """Hit the data-download page once so the server sets any session cookies the
    download endpoint may check."""
    try:
        with _opener.open(
            f"{HOST}/data-download/nationwide-data?version=jun2025&pubDataVer=jun2025",
            timeout=60,
        ) as r:
            r.read(1)
    except Exception as e:
        print(f"  (warm_session: {e} -- continuing anyway)", file=sys.stderr)


def get_json(path: str, **params):
    url = f"{HOST}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with _opener.open(url, timeout=60) as r:
        return json.loads(r.read())


def stream(file_id, dest: Path, expect_size=None, retries: int = 4):
    url = f"{HOST}/nbm/map/api/getNBMDataDownloadFile/{file_id}/1"
    last = None
    for i in range(retries):
        try:
            with _opener.open(url, timeout=600) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if expect_size and dest.stat().st_size != expect_size:
                raise IOError(f"size mismatch {dest.stat().st_size} vs {expect_size}")
            return
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-only", action="store_true", help="dry run; print file list and exit")
    ap.add_argument("--only", default="", help="comma-sep state abbrs to limit to (e.g. DC,DE)")
    ap.add_argument("--keep-zips", action="store_true", help="keep raw zips in _zips/")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    warm_session()

    # 1) Discover the Jun 2025 filing
    print("GET /nbm/map/api/published/filing")
    filings = get_json("/nbm/map/api/published/filing")
    preview = json.dumps(filings, indent=2, default=str)
    print(preview[:1500] + ("..." if len(preview) > 1500 else ""))

    seq = filings if isinstance(filings, list) else filings.get("data", filings)
    filing = next(
        (f for f in seq if TARGET_FILING in json.dumps(f, default=str).lower()),
        None,
    )
    if filing is None:
        print(f"\nNo filing matched '{TARGET_FILING}'. Inspect the JSON above and adjust "
              "TARGET_FILING.", file=sys.stderr)
        sys.exit(2)
    uuid = filing.get("process_uuid") or filing.get("id") or filing.get("filing_id")
    print(f"\nFiling matched: {filing}\n  uuid={uuid}\n")

    # 2) List Fixed Broadband Availability files, all techs, all states
    print("GET /nbm/map/api/published/published_reports")
    listing = get_json(
        "/nbm/map/api/published/published_reports",
        process_uuid=uuid,
        data_type="availability",
        filing_subtype="fixed_broadband",
        technology_code=-1,  # -1 == "All Technologies" sentinel; verify in printed sample
    )
    files = listing.get("data", listing) if isinstance(listing, dict) else listing
    if not isinstance(files, list):
        print("Unexpected listing shape:", json.dumps(listing, indent=2, default=str)[:2000],
              file=sys.stderr)
        sys.exit(2)
    print(f"  Files returned: {len(files)}")
    if files:
        print("  Sample:", json.dumps(files[0], indent=2, default=str))

    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        files = [
            f for f in files
            if (f.get("state_abbr") or f.get("state_code") or "").upper() in wanted
        ]
        print(f"  After --only filter: {len(files)} files")

    total_bytes = sum(int(f.get("file_size") or 0) for f in files)
    print(f"  Total: {total_bytes / 1e9:.2f} GB across {len(files)} files")

    if args.list_only:
        return

    # 3) Download + extract in parallel
    zips = OUT / "_zips"
    zips.mkdir(exist_ok=True)

    def fetch(meta):
        fid = meta.get("file_id") or meta.get("id")
        name = meta.get("file_name") or f"{fid}.zip"
        size = int(meta.get("file_size") or 0)
        zp = zips / name
        if not (zp.exists() and size and zp.stat().st_size == size):
            stream(fid, zp, expect_size=size or None)
        target_name = (
            meta.get("state_name") or meta.get("state_abbr") or "_unknown"
        ).replace(" ", "_")
        target = OUT / target_name
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            z.extractall(target)
        if not args.keep_zips:
            zp.unlink()
        return f"{target_name} ({name}, {zp.stat().st_size if zp.exists() else size} B)"

    print(f"\nDownloading with {args.workers} workers...")
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch, m): m for m in files}
        for fut in as_completed(futs):
            try:
                print(f"  ok  {fut.result()}")
            except Exception as e:
                m = futs[fut]
                msg = f"{m.get('state_name') or m.get('state_abbr')}: {e}"
                errors.append(msg)
                print(f"  ERR {msg}", file=sys.stderr)

    # 4) Manifest
    (OUT / "manifest.json").write_text(json.dumps(
        {"filing": filing, "files": files, "errors": errors},
        indent=2,
        default=str,
    ))
    print(f"\nDone. Output: {OUT.resolve()}  ({len(errors)} errors)")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
