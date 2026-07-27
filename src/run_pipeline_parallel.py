"""Parallel version of the end-to-end pipeline across both sites.

Same end result as `run_pipeline.py`, but overlaps the independent parts:

1. Metrocuadrado's stage-1 scrape runs concurrently with fincaraiz's scrape
   (different sites, different output files, no shared state).
2. Once metrocuadrado stage 1 finishes, candidates are selected (checked
   against raw `price`, since `total_price` doesn't exist pre-enrichment)
   and enriched in chunks of `chunk_size` listings, each chunk on its own
   browser, up to `max_concurrent_browsers` running at once.

`run_pipeline.py` stays as the sequential reference implementation; this is
a separate script, not a replacement.
"""

import argparse
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from paths import (
    CHROME_PROFILE_DIR,
    FINCARAIZ_LISTINGS_PATH,
    LISTINGS_PATH,
    MERGED_LISTINGS_PATH,
    write_json,
)
from run_pipeline import ListingFilters
from scrape_fincaraiz import scrape_listings as scrape_fincaraiz_listings
from scrape_listing_details import enrich_listing
from scrape_metrocuadrado import scrape_listings as scrape_metrocuadrado_listings
from stealth_browser import StealthBrowser


def _enrich_chunk(
    chunk_index: int,
    chunk: list[dict],
    headless: bool,
    delay_between_listings: tuple[float, float] = (0.5, 2.0),
) -> dict:
    """Enrich one chunk with its own isolated browser + profile dir.
    Mutates each entry in `chunk` in place (same dict objects the caller's
    master list holds) — the return value is a summary for logging only.
    """
    profile_dir = str(CHROME_PROFILE_DIR / f"chunk_{chunk_index:03d}")
    label = f"chunk {chunk_index}"
    started = time.monotonic()
    succeeded, failed = 0, []

    print(f"[{label}] starting: {len(chunk)} listings")
    try:
        with StealthBrowser(
            capture_network=True, headless=headless, user_data_dir=profile_dir
        ) as browser:
            for i, entry in enumerate(chunk, 1):
                url = entry.get("url")
                try:
                    enrich_listing(browser, entry)
                except Exception as exc:
                    failed.append((url, str(exc)))
                    print(f"[{label}] [{i}/{len(chunk)}] FAILED {url}: {exc!r}")
                else:
                    succeeded += 1
                    print(f"[{label}] [{i}/{len(chunk)}] OK {url}")
                if i < len(chunk):
                    time.sleep(random.uniform(*delay_between_listings))
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(
            f"[{label}] ABORTED after {elapsed:.1f}s, "
            f"{succeeded}/{len(chunk)} done before abort: {exc!r}"
        )
        return {
            "chunk_index": chunk_index,
            "total": len(chunk),
            "succeeded": succeeded,
            "failed": failed,
            "fatal_error": str(exc),
        }

    elapsed = time.monotonic() - started
    print(
        f"[{label}] done in {elapsed:.1f}s: "
        f"{succeeded}/{len(chunk)} succeeded, {len(failed)} failed"
    )
    return {
        "chunk_index": chunk_index,
        "total": len(chunk),
        "succeeded": succeeded,
        "failed": failed,
        "fatal_error": None,
    }


def _run_enrichment_fanout(
    candidates: list[dict],
    chunk_size: int,
    max_concurrent_browsers: int,
    headless: bool,
) -> list[dict]:
    chunks = [candidates[i : i + chunk_size] for i in range(0, len(candidates), chunk_size)]
    if not chunks:
        print("  -> no candidates matched; skipping enrichment fan-out")
        return []

    worker_count = max(1, min(max_concurrent_browsers, len(chunks)))
    print(
        f"  -> fanning out {len(candidates)} candidates across {len(chunks)} "
        f"chunk(s) of up to {chunk_size}, {worker_count} browser(s) concurrently"
    )

    results = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_enrich_chunk, i, chunk, headless) for i, chunk in enumerate(chunks)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda r: r["chunk_index"])


def run_pipeline_parallel(
    filters: ListingFilters,
    metrocuadrado_pages: int = 30,
    fincaraiz_pages: int = 10,
    headless: bool = False,
    enrich_headless: bool = True,
    chunk_size: int = 25,
    max_concurrent_browsers: int = 4,
) -> list[dict]:
    print("=== 1/3: scraping metrocuadrado + fincaraiz concurrently ===")
    with ThreadPoolExecutor(max_workers=2) as executor:
        mc_future = executor.submit(
            scrape_metrocuadrado_listings, max_pages=metrocuadrado_pages, headless=headless
        )
        fr_future = executor.submit(scrape_fincaraiz_listings, max_pages=fincaraiz_pages)

        metrocuadrado_listings = mc_future.result()
        write_json(LISTINGS_PATH, metrocuadrado_listings)
        print(f"  -> {len(metrocuadrado_listings)} metrocuadrado listings saved")

        fincaraiz_listings = fr_future.result()
        write_json(FINCARAIZ_LISTINGS_PATH, fincaraiz_listings)
        print(f"  -> {len(fincaraiz_listings)} fincaraiz listings saved")

    print("=== 2/3: selecting + enriching metrocuadrado candidates (fanned out) ===")
    candidates = [
        listing for listing in metrocuadrado_listings if filters.matches(listing, price_field="price")
    ]
    print(f"  -> {len(candidates)}/{len(metrocuadrado_listings)} match filters (by listed price)")

    chunk_results = _run_enrichment_fanout(
        candidates,
        chunk_size=chunk_size,
        max_concurrent_browsers=max_concurrent_browsers,
        headless=enrich_headless,
    )
    total_ok = sum(r["succeeded"] for r in chunk_results)
    total_failed = sum(len(r["failed"]) for r in chunk_results)
    aborted = [r["chunk_index"] for r in chunk_results if r["fatal_error"]]
    print(
        f"  -> enrichment done: {total_ok} succeeded, {total_failed} failed"
        + (f", chunk(s) aborted early: {aborted}" if aborted else "")
    )
    write_json(LISTINGS_PATH, metrocuadrado_listings)

    print("=== 3/3: merging ===")
    metrocuadrado_final = [
        listing for listing in metrocuadrado_listings if filters.matches(listing, price_field="total_price")
    ]
    fincaraiz_final = [
        listing for listing in fincaraiz_listings if filters.matches(listing, price_field="total_price")
    ]
    for listing in metrocuadrado_final:
        listing["source"] = "metrocuadrado"
    for listing in fincaraiz_final:
        listing["source"] = "fincaraiz"

    merged = metrocuadrado_final + fincaraiz_final
    write_json(MERGED_LISTINGS_PATH, merged)
    print(
        f"  -> {len(metrocuadrado_final)} metrocuadrado + {len(fincaraiz_final)} fincaraiz "
        f"= {len(merged)} listings saved to {MERGED_LISTINGS_PATH.name}"
    )
    return merged


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parallel metrocuadrado + fincaraiz scrape/enrich/merge pipeline."
    )
    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument("--min-area", type=float, default=None, dest="min_area_m2")
    parser.add_argument("--max-area", type=float, default=None, dest="max_area_m2")
    parser.add_argument("--min-bathrooms", type=int, default=None)
    parser.add_argument("--max-bathrooms", type=int, default=None)
    parser.add_argument("--min-parking", type=int, default=None, dest="min_parking_spots")
    parser.add_argument("--max-parking", type=int, default=None, dest="max_parking_spots")
    parser.add_argument("--metrocuadrado-pages", type=int, default=30)
    parser.add_argument("--fincaraiz-pages", type=int, default=10)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run metrocuadrado stage-1's browser headless (default: headed).",
    )
    parser.add_argument(
        "--enrich-headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run enrichment fan-out browsers headless (default: True). "
        "Pass --no-enrich-headless to see them.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=25,
        help="Candidates per enrichment browser session (default: 25).",
    )
    parser.add_argument(
        "--max-concurrent-browsers",
        type=int,
        default=4,
        help="Max enrichment browsers running at once (default: 4).",
    )
    args = parser.parse_args()

    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")
    if args.max_concurrent_browsers < 1:
        parser.error("--max-concurrent-browsers must be >= 1")

    return args


if __name__ == "__main__":
    args = _parse_args()
    filters = ListingFilters(
        min_price=args.min_price,
        max_price=args.max_price,
        min_area_m2=args.min_area_m2,
        max_area_m2=args.max_area_m2,
        min_bathrooms=args.min_bathrooms,
        max_bathrooms=args.max_bathrooms,
        min_parking_spots=args.min_parking_spots,
        max_parking_spots=args.max_parking_spots,
    )
    run_pipeline_parallel(
        filters,
        metrocuadrado_pages=args.metrocuadrado_pages,
        fincaraiz_pages=args.fincaraiz_pages,
        headless=args.headless,
        enrich_headless=args.enrich_headless,
        chunk_size=args.chunk_size,
        max_concurrent_browsers=args.max_concurrent_browsers,
    )
