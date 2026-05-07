#!/usr/bin/env python3
"""
Finds media release links in merger HTML pages, downloads each release,
and saves the content into the corresponding detail/<id>.json under a
'media_release' key.

Usage:
    python3 fetch_media_releases.py [--pages-dir pages] [--detail-dir detail]
"""

import argparse
import json
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.accc.gov.au"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; accc-merger-scraper/1.0; research purposes)"
    )
}


def find_media_release_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("div.field--name-field-accc-body")
    if not body:
        return None
    for a in body.find_all("a", href=True):
        href = a["href"]
        if "/media-release/" in href:
            return BASE_URL + href if href.startswith("/") else href
    return None


def fetch_media_release(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def field_text(name: str) -> str | None:
        el = soup.select_one(f"div.field--name-{name}")
        return el.get_text(separator=" ", strip=True) if el else None

    # Title: prefer h1 inside article, fall back to field--name-title
    title_el = soup.select_one("article h1, h1.page-title, div.field--name-title h1")
    title = title_el.get_text(strip=True) if title_el else field_text("field-acccgov-title")

    # Published date - strip the label prefix "Date"
    raw_date = field_text("field-accc-news-published-date")
    date = raw_date.removeprefix("Date").strip() if raw_date else None

    # Reference number - strip label prefix "Release number"
    raw_ref = field_text("field-accc-news-reference-number")
    reference_number = raw_ref.removeprefix("Release number").strip() if raw_ref else None

    # Main body
    body_el = soup.select_one("div.field--name-field-acccgov-body")
    body_text = body_el.get_text(separator="\n", strip=True) if body_el else None

    # Media enquiries
    enquiries_el = soup.select_one("div.field--name-field-acccgov-news-enquiries")
    enquiries = enquiries_el.get_text(separator=" ", strip=True) if enquiries_el else None

    return {
        "url": url,
        "title": title,
        "date": date,
        "reference_number": reference_number,
        "body_text": body_text,
        "enquiries": enquiries,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fetch media releases linked from merger pages"
    )
    parser.add_argument("--pages-dir", default="pages")
    parser.add_argument("--detail-dir", default="detail")
    args = parser.parse_args()

    pages_dir = Path(args.pages_dir)
    detail_dir = Path(args.detail_dir)

    if not detail_dir.exists():
        print(f"Error: {detail_dir} not found. Run extract.py first.", file=sys.stderr)
        sys.exit(1)

    found = 0
    saved = 0
    errors = 0

    for html_path in sorted(pages_dir.glob("*.html")):
        mid = html_path.stem
        url = find_media_release_url(html_path.read_text(errors="replace"))
        if not url:
            continue

        found += 1
        detail_path = detail_dir / f"{mid}.json"
        print(f"{mid}: {url}")

        try:
            release = fetch_media_release(url)
            print(f"  -> {release['title']!r}  ({release['date']})")

            if detail_path.exists():
                with open(detail_path, encoding="utf-8") as f:
                    detail = json.load(f)
            else:
                detail = {"id": mid}

            detail["media_release"] = release

            with open(detail_path, "w", encoding="utf-8") as f:
                json.dump(detail, f, indent=2, ensure_ascii=False)

            saved += 1
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            errors += 1

    print(f"\nFound {found} media release links. Saved={saved}  Errors={errors}")


if __name__ == "__main__":
    main()
