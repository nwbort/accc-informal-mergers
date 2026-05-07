#!/usr/bin/env python3
"""
Parses downloaded merger HTML pages, finds all document tables (market inquiries,
statement of issues, undertakings, etc.), and downloads each PDF/doc into
docs/<id>/<filename>.

Also updates mergers.json with a 'documents' list for each merger.

Usage:
    python3 fetch_docs.py [--input mergers.json] [--pages-dir pages]
                          [--docs-dir docs] [--workers 20]
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.accc.gov.au"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; accc-merger-scraper/1.0; research purposes)"
    )
}

_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def preceding_heading(table) -> str | None:
    """Return the text of the nearest h2/h3/h4 that precedes this table."""
    for el in table.find_all_previous(["h2", "h3", "h4"]):
        text = el.get_text(strip=True)
        if text:
            return text
    return None


def parse_docs(html: str) -> list[dict]:
    """Extract all document entries from all tables on a merger page."""
    soup = BeautifulSoup(html, "html.parser")
    docs = []

    for table in soup.select("table.views-table"):
        section = preceding_heading(table)

        for row in table.select("tr.contextual-region"):
            file_span = row.select_one("span.file")
            if not file_span:
                continue

            # Primary anchor has aria-label (doc title) and the download URL
            a = file_span.select_one("a[href]")
            if not a:
                continue

            href = a.get("href", "")
            # Only care about files in the public-registers documents path
            if "/system/files/public-registers/" not in href:
                continue

            # Prefer the ?ref=0&download=y variant if present, else plain href
            download_url = BASE_URL + href if href.startswith("/") else href

            # Canonical URL (no query string) for deduplication / filename
            clean_path = href.split("?")[0]
            filename = unquote(os.path.basename(clean_path))

            title = a.get("aria-label") or a.get_text(strip=True) or filename

            # Date from the sibling <time> element in the row
            time_el = row.select_one("time")
            date_iso = time_el.get("datetime") if time_el else None
            date_str = time_el.get_text(strip=True) if time_el else None

            docs.append(
                {
                    "section": section,
                    "title": title,
                    "filename": filename,
                    "download_url": download_url,
                    "date_iso": date_iso,
                    "date": date_str,
                }
            )

    # Deduplicate by filename (same file can appear in multiple tables on rare pages)
    seen: set[str] = set()
    unique = []
    for d in docs:
        if d["filename"] not in seen:
            seen.add(d["filename"])
            unique.append(d)
    return unique


def download_doc(doc: dict, dest_dir: Path) -> tuple[str, str]:
    """Returns (filename, status) where status is 'saved', 'skipped', or 'error:<msg>'."""
    dest = dest_dir / doc["filename"]
    if dest.exists():
        return doc["filename"], "skipped"
    try:
        session = get_session()
        resp = session.get(doc["download_url"], headers=HEADERS, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return doc["filename"], "saved"
    except Exception as exc:
        return doc["filename"], f"error:{exc}"


def process_merger(merger: dict, pages_dir: Path, docs_dir: Path) -> dict:
    """Parse docs from saved HTML and download them. Returns updated merger dict."""
    mid = merger["id"]
    html_path = pages_dir / f"{mid}.html"

    if not html_path.exists():
        merger["documents"] = []
        return merger

    html = html_path.read_text(errors="replace")
    docs = parse_docs(html)

    if docs:
        dest_dir = docs_dir / mid
        dest_dir.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            _, status = download_doc(doc, dest_dir)
            doc["local_path"] = str(dest_dir / doc["filename"]) if status in ("saved", "skipped") else None
            doc["download_status"] = status

    merger["documents"] = docs
    return merger


def main():
    parser = argparse.ArgumentParser(
        description="Download documents linked from merger HTML pages"
    )
    parser.add_argument("--input", default="mergers.json")
    parser.add_argument("--pages-dir", default="pages")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    mergers = data["mergers"]
    pages_dir = Path(args.pages_dir)
    docs_dir = Path(args.docs_dir)
    docs_dir.mkdir(exist_ok=True)

    total = len(mergers)
    saved = skipped = errors = no_docs = done = 0
    lock = threading.Lock()
    updated_mergers: dict[str, dict] = {}

    print(f"Processing {total} merger pages → {docs_dir}/", flush=True)
    print(f"Workers: {args.workers}\n", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_merger, m, pages_dir, docs_dir): m
            for m in mergers
        }
        for future in as_completed(futures):
            merger = future.result()
            docs = merger.get("documents", [])

            with lock:
                updated_mergers[merger["id"]] = merger
                done += 1
                if not docs:
                    no_docs += 1
                for d in docs:
                    st = d.get("download_status", "")
                    if st == "saved":
                        saved += 1
                    elif st == "skipped":
                        skipped += 1
                    elif st.startswith("error"):
                        errors += 1
                        print(f"  WARN {merger['id']} {d['filename']}: {st}", flush=True)

                if done % 100 == 0 or done == total:
                    print(
                        f"  {done}/{total} mergers  docs saved={saved}"
                        f"  skipped={skipped}  errors={errors}"
                        f"  no-docs={no_docs}",
                        flush=True,
                    )

    # Reassemble in original order
    data["mergers"] = [updated_mergers[m["id"]] for m in mergers]

    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(
        f"\nDone. docs saved={saved}  skipped={skipped}  errors={errors}"
        f"  mergers with no docs={no_docs}"
    )
    print(f"mergers.json updated with document metadata.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
