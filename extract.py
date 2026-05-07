#!/usr/bin/env python3
"""
Parses downloaded merger HTML pages and saves structured detail JSON to
detail/<id>.json for each merger.

Fields extracted (where present):
  id, title, url, status, outcome, reference_number, industries,
  date_completed / _iso, date_commenced / _iso, total_review_days,
  acquirers, targets, timeline, body_sections (Summary, Market definition,
  Competition analysis, Undertakings, Resolution, etc.)

Usage:
    python3 extract.py [--input mergers.json] [--pages-dir pages]
                       [--detail-dir detail] [--workers 8]
"""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# Headings that are clearly not section names (malformed data)
JUNK_HEADINGS = {"Igneo", "Benedict Recycling", "Mitsubishi Fuso", "Hino Motors Limited", ""}


def text(el) -> str | None:
    """Stripped plain text from an element, or None if missing/empty."""
    if el is None:
        return None
    t = el.get_text(separator=" ", strip=True)
    return t or None


def field(soup: BeautifulSoup, name: str):
    """Return the div for a given field--name-* class."""
    return soup.select_one(f"div.field--name-{name}")


def field_item_text(soup: BeautifulSoup, name: str) -> str | None:
    el = field(soup, name)
    if el is None:
        return None
    item = el.select_one(".field__item")
    return text(item)


def field_items_text(soup: BeautifulSoup, name: str) -> list[str]:
    el = field(soup, name)
    if el is None:
        return []
    return [t for item in el.select(".field__item") if (t := text(item))]


def parse_datetime_field(soup: BeautifulSoup, name: str) -> tuple[str | None, str | None]:
    """Returns (human_text, iso_string) for a datetime field."""
    el = field(soup, name)
    if el is None:
        return None, None
    time_el = el.select_one("time")
    if time_el:
        return text(time_el), time_el.get("datetime")
    item = el.select_one(".field__item")
    return text(item), None


def parse_entity_list(soup: BeautifulSoup, name: str) -> list[str]:
    """Extract a list of entity names (acquirers / targets)."""
    el = field(soup, name)
    if el is None:
        return []
    return [
        t for p in el.select("div.paragraph")
        if (t := text(p))
    ]


def parse_timeline(soup: BeautifulSoup) -> list[dict]:
    el = field(soup, "field-acccgov-timeline")
    if el is None:
        return []
    rows = []
    for tr in el.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        time_el = cells[0].find("time")
        date_iso = time_el.get("datetime") if time_el else None
        date_str = text(cells[0])
        event_str = text(cells[1])
        rows.append({"date": date_str, "date_iso": date_iso, "event": event_str})
    return rows


def parse_body_sections(soup: BeautifulSoup) -> dict[str, str]:
    """
    Split field--name-field-accc-body into sections keyed by heading text.
    Text before the first heading is stored under '' (empty string key).
    Junk/company-name headings are treated as plain text, not section dividers.
    """
    el = field(soup, "field-accc-body")
    if el is None:
        return {}

    sections: dict[str, list[str]] = {}
    current_key = ""
    sections[current_key] = []

    for child in el.children:
        if not isinstance(child, Tag):
            stripped = child.get_text(strip=True)
            if stripped:
                sections[current_key].append(stripped)
            continue

        if child.name in ("h2", "h3", "h4"):
            heading = child.get_text(strip=True)
            if heading in JUNK_HEADINGS:
                # treat as body text
                sections[current_key].append(heading)
            else:
                current_key = heading
                if current_key not in sections:
                    sections[current_key] = []
        else:
            t = child.get_text(separator=" ", strip=True)
            if t:
                sections[current_key].append(t)

    # Join accumulated text, drop empty-string lead key if empty
    result = {}
    for k, parts in sections.items():
        combined = " ".join(parts).strip()
        if combined:
            result[k] = combined
    # Remove the empty preamble key if blank
    result.pop("", None)
    return result


def extract(merger: dict, pages_dir: Path) -> dict:
    mid = merger["id"]
    html_path = pages_dir / f"{mid}.html"

    detail: dict = {
        "id": mid,
        "title": merger.get("title"),
        "url": merger.get("url"),
    }

    if not html_path.exists():
        detail["parse_error"] = "html not found"
        return detail

    soup = BeautifulSoup(html_path.read_text(errors="replace"), "html.parser")

    # --- Structured sidebar fields ---
    detail["status"] = field_item_text(soup, "field-acccgov-pub-reg-status")
    detail["outcome"] = field_item_text(soup, "field-acccgov-pub-reg-outcome")
    detail["reference_number"] = field_item_text(soup, "field-acccgov-ref-number")
    detail["industries"] = field_items_text(soup, "field-acccgov-industry")

    date_completed, date_completed_iso = parse_datetime_field(
        soup, "field-acccgov-pub-reg-end-date"
    )
    detail["date_completed"] = date_completed
    detail["date_completed_iso"] = date_completed_iso

    date_commenced, date_commenced_iso = parse_datetime_field(
        soup, "field-acccgov-pub-reg-date"
    )
    detail["date_commenced"] = date_commenced
    detail["date_commenced_iso"] = date_commenced_iso

    # Total review days stored as integer
    days_el = field(soup, "field-acccgov-total-review-days")
    if days_el:
        item = days_el.select_one(".field__item")
        raw = item.get("content") if item else None
        try:
            detail["total_review_days"] = int(raw) if raw else None
        except ValueError:
            detail["total_review_days"] = text(item)
    else:
        detail["total_review_days"] = None

    detail["acquirers"] = parse_entity_list(soup, "field-acccgov-applicants")
    detail["targets"] = parse_entity_list(soup, "field-acccgov-pub-reg-targets")

    # --- Timeline ---
    detail["timeline"] = parse_timeline(soup)

    # --- Body sections ---
    detail["body_sections"] = parse_body_sections(soup)

    return detail


def process(merger: dict, pages_dir: Path, detail_dir: Path) -> str:
    mid = merger["id"]
    dest = detail_dir / f"{mid}.json"
    detail = extract(merger, pages_dir)
    dest.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")
    return mid


def main():
    parser = argparse.ArgumentParser(description="Extract structured detail JSON from merger HTML pages")
    parser.add_argument("--input", default="mergers.json")
    parser.add_argument("--pages-dir", default="pages")
    parser.add_argument("--detail-dir", default="detail")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    mergers = data["mergers"]
    pages_dir = Path(args.pages_dir)
    detail_dir = Path(args.detail_dir)
    detail_dir.mkdir(exist_ok=True)

    total = len(mergers)
    done = 0
    lock = threading.Lock()

    print(f"Extracting {total} merger pages → {detail_dir}/", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process, m, pages_dir, detail_dir): m for m in mergers
        }
        for future in as_completed(futures):
            future.result()
            with lock:
                done += 1
                if done % 200 == 0 or done == total:
                    print(f"  {done}/{total}", flush=True)

    print(f"\nDone. {total} detail JSONs saved to {detail_dir}/")


if __name__ == "__main__":
    main()
