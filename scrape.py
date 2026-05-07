#!/usr/bin/env python3
"""
Scrapes the ACCC informal merger reviews public register and saves results to JSON.

Usage:
    python3 scrape.py [--output mergers.json] [--workers 10]
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.accc.gov.au"
REGISTER_URL = (
    BASE_URL
    + "/public-registers/browse-public-registers"
    "?f%5B0%5D=type%3Aacccgov_informal_merger_review"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; accc-merger-scraper/1.0; research purposes)"
    )
}


def page_url(page: int) -> str:
    return REGISTER_URL + (f"&page={page}" if page > 0 else "")


def fetch_html(session: requests.Session, url: str) -> str:
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def get_total_pages(soup: BeautifulSoup) -> int:
    last_link = soup.select_one(
        "li.pager__item--last a, li.page-item a[aria-label='Last page']"
    )
    if last_link:
        href = last_link.get("href", "")
        for part in href.split("&"):
            if part.startswith("page="):
                return int(part.split("=")[1]) + 1

    page_links = soup.select("ul.pager__items li a.page-link")
    max_page = 0
    for link in page_links:
        href = link.get("href", "")
        for part in href.split("&"):
            if part.startswith("page="):
                try:
                    max_page = max(max_page, int(part.split("=")[1]))
                except ValueError:
                    pass
    return max_page + 1 if max_page else 1


def parse_cards(soup: BeautifulSoup) -> list[dict]:
    results = []
    for article in soup.select("article.accc-card--clickable"):
        entry: dict = {}

        title_link = article.select_one("div.accc-card__title h3 a")
        if title_link:
            entry["title"] = title_link.get_text(strip=True)
            href = title_link.get("href", "")
            entry["url"] = BASE_URL + href if href.startswith("/") else href
        else:
            continue

        footer = article.select_one("footer.accc-card__metadata")
        if not footer:
            results.append(entry)
            continue

        outcome_el = footer.select_one(
            "div.field--name-field-acccgov-pub-reg-outcome .field__item"
        )
        entry["outcome"] = outcome_el.get_text(strip=True) if outcome_el else None

        time_el = footer.select_one(
            "div.field--name-field-acccgov-pub-reg-end-date time"
        )
        if time_el:
            entry["date_completed_iso"] = time_el.get("datetime", None)
            entry["date_completed"] = time_el.get_text(strip=True)
        else:
            date_el = footer.select_one(
                "div.field--name-field-acccgov-pub-reg-end-date .field__item"
            )
            entry["date_completed_iso"] = None
            entry["date_completed"] = (
                date_el.get_text(strip=True) if date_el else None
            )

        industry_items = footer.select(
            "div.field--name-field-acccgov-industry .field__item"
        )
        industries = [i.get_text(strip=True) for i in industry_items]
        entry["industries"] = industries
        entry["industry"] = (
            industries[0] if len(industries) == 1 else (industries or None)
        )

        status_el = footer.select_one(
            "div.field--name-field-acccgov-pub-reg-status .field__item"
        )
        entry["status"] = status_el.get_text(strip=True) if status_el else None

        results.append(entry)
    return results


def fetch_page_entries(
    session: requests.Session, page: int
) -> tuple[int, list[dict]]:
    html = fetch_html(session, page_url(page))
    soup = BeautifulSoup(html, "html.parser")
    return page, parse_cards(soup)


def scrape(workers: int = 10) -> list[dict]:
    session = requests.Session()

    print("Fetching page 1 to determine total pages...", flush=True)
    first_html = fetch_html(session, page_url(0))
    first_soup = BeautifulSoup(first_html, "html.parser")
    total_pages = get_total_pages(first_soup)
    print(f"  {total_pages} pages to fetch", flush=True)

    # page 0 already done; fetch the rest in parallel
    pages_by_number: dict[int, list[dict]] = {0: parse_cards(first_soup)}
    completed = 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_page_entries, session, p): p
            for p in range(1, total_pages)
        }
        for future in as_completed(futures):
            page_num, cards = future.result()
            pages_by_number[page_num] = cards
            completed += 1
            print(
                f"  [{completed}/{total_pages}] page {page_num + 1}: {len(cards)} entries",
                flush=True,
            )

    # reassemble in page order and assign sequential IDs
    all_mergers: list[dict] = []
    for p in range(total_pages):
        all_mergers.extend(pages_by_number[p])

    id_width = len(str(len(all_mergers)))
    for i, merger in enumerate(all_mergers, start=1):
        merger["id"] = str(i).zfill(id_width)

    return all_mergers


def main():
    parser = argparse.ArgumentParser(description="Scrape ACCC informal merger register")
    parser.add_argument("--output", default="mergers.json", help="Output JSON file")
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Parallel workers for page fetching (default: 10)",
    )
    args = parser.parse_args()

    print("Scraping ACCC informal merger register...", flush=True)
    mergers = scrape(workers=args.workers)

    output = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source_url": REGISTER_URL,
        "total_count": len(mergers),
        "mergers": mergers,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone. {len(mergers)} mergers saved to {args.output}")


if __name__ == "__main__":
    main()
