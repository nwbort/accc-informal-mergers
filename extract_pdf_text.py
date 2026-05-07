#!/usr/bin/env python3
"""
Extracts text from every PDF in docs/<id>/ and saves it alongside as
docs/<id>/<filename>.txt.

Already-extracted files are skipped, so the script is safe to re-run.

Usage:
    python3 extract_pdf_text.py [--docs-dir docs] [--workers 8]
"""

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf


def extract_one(pdf_path: Path) -> tuple[Path, str]:
    """Returns (pdf_path, status) where status is 'saved', 'skipped', or 'error:<msg>'."""
    txt_path = pdf_path.with_suffix(".txt")
    if txt_path.exists():
        return pdf_path, "skipped"

    try:
        doc = pymupdf.open(pdf_path)
        pages = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text)
        full_text = "\n\n".join(pages)
        txt_path.write_text(full_text, encoding="utf-8")
        return pdf_path, "saved"
    except Exception as exc:
        return pdf_path, f"error:{exc}"


def main():
    parser = argparse.ArgumentParser(description="Extract text from merger PDFs")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir)
    if not docs_dir.exists():
        print(f"Error: {docs_dir} not found.", file=sys.stderr)
        sys.exit(1)

    pdfs = sorted(docs_dir.rglob("*.pdf"))
    total = len(pdfs)
    saved = skipped = errors = done = 0
    lock = threading.Lock()

    print(f"Extracting text from {total} PDFs (workers={args.workers})", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract_one, p): p for p in pdfs}
        for future in as_completed(futures):
            pdf_path, status = future.result()
            with lock:
                done += 1
                if status == "saved":
                    saved += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    errors += 1
                    print(f"  WARN {pdf_path}: {status}", flush=True)

                if done % 100 == 0 or done == total:
                    print(
                        f"  {done}/{total}  saved={saved}  skipped={skipped}  errors={errors}",
                        flush=True,
                    )

    print(f"\nDone. saved={saved}  skipped={skipped}  errors={errors}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
