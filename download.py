#!/usr/bin/env python3
"""
Downloads the HTML detail page for each merger in mergers.json.

Files are saved to ./pages/<id>.html  (e.g. pages/0001.html).
Already-downloaded files are skipped, so the script is safe to re-run.

Usage:
    python3 download.py [--input mergers.json] [--pages-dir pages] [--workers 20]
"""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; accc-merger-scraper/1.0; research purposes)"
    )
}

# Thread-local sessions so each worker reuses its own connection pool
_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def download_one(merger: dict, pages_dir: Path) -> tuple[str, str]:
    """Returns (id, status) where status is 'saved', 'skipped', or 'error:<msg>'."""
    mid = merger["id"]
    dest = pages_dir / f"{mid}.html"

    if dest.exists():
        return mid, "skipped"

    try:
        session = get_session()
        resp = session.get(merger["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return mid, "saved"
    except Exception as exc:
        return mid, f"error:{exc}"


def main():
    parser = argparse.ArgumentParser(
        description="Download HTML pages for each merger in mergers.json"
    )
    parser.add_argument("--input", default="mergers.json", help="Input JSON file")
    parser.add_argument(
        "--pages-dir", default="pages", help="Directory to save HTML files"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Parallel download workers (default: 20)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found. Run scrape.py first.", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    mergers = data["mergers"]
    pages_dir = Path(args.pages_dir)
    pages_dir.mkdir(exist_ok=True)

    total = len(mergers)
    saved = skipped = errors = 0
    lock = threading.Lock()
    done = 0

    print(f"Downloading {total} merger pages → {pages_dir}/", flush=True)
    print(f"Workers: {args.workers}  (already-downloaded files will be skipped)\n", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_one, m, pages_dir): m for m in mergers
        }
        for future in as_completed(futures):
            mid, status = future.result()
            with lock:
                done += 1
                if status == "saved":
                    saved += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    errors += 1
                    print(f"  WARN {mid}: {status}", flush=True)

                if done % 50 == 0 or done == total:
                    print(
                        f"  {done}/{total}  saved={saved}  skipped={skipped}  errors={errors}",
                        flush=True,
                    )

    print(f"\nDone. saved={saved}  skipped={skipped}  errors={errors}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
