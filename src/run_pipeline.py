"""End-to-end pipeline across both sites.

1. Scrape metrocuadrado search results.
2. Select the metrocuadrado listings matching the given price/area_m2/
   bathrooms/parking_spots ranges (checked against the raw `price`, since
   `total_price` doesn't exist until after enrichment) and enrich just that
   subset.
3. Scrape fincaraiz search results (already fully enriched per listing).
4. Merge: re-filter the now-enriched metrocuadrado listings by the same
   ranges (this time checked against `total_price`, now that it's known) and
   filter fincaraiz listings by the same ranges (also against `total_price`,
   which it has natively) — then concatenate both into one file.
"""

import argparse
import json
from dataclasses import dataclass

from paths import FINCARAIZ_LISTINGS_PATH, LISTINGS_PATH, MERGED_LISTINGS_PATH, write_json
from scrape_fincaraiz import scrape_listings as scrape_fincaraiz_listings
from scrape_listing_details import enrich_listings
from scrape_metrocuadrado import scrape_listings as scrape_metrocuadrado_listings


@dataclass
class ListingFilters:
    min_price: float | None = None
    max_price: float | None = None
    min_area_m2: float | None = None
    max_area_m2: float | None = None
    min_bathrooms: int | None = None
    max_bathrooms: int | None = None
    min_parking_spots: int | None = None
    max_parking_spots: int | None = None

    def matches(self, listing: dict, price_field: str) -> bool:
        checks = (
            (listing.get(price_field), self.min_price, self.max_price),
            (listing.get("area_m2"), self.min_area_m2, self.max_area_m2),
            (listing.get("bathrooms"), self.min_bathrooms, self.max_bathrooms),
            (listing.get("parking_spots"), self.min_parking_spots, self.max_parking_spots),
        )
        for value, lo, hi in checks:
            if lo is None and hi is None:
                continue
            if value is None:
                return False
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
        return True


def run_pipeline(
    filters: ListingFilters,
    metrocuadrado_pages: int = 30,
    fincaraiz_pages: int = 10,
    headless: bool = False,
) -> list[dict]:
    print(f"=== 1/4: scraping metrocuadrado ({metrocuadrado_pages} pages) ===")
    metrocuadrado_listings = scrape_metrocuadrado_listings(
        max_pages=metrocuadrado_pages, headless=headless
    )
    write_json(LISTINGS_PATH, metrocuadrado_listings)
    print(f"  -> {len(metrocuadrado_listings)} listings saved to {LISTINGS_PATH.name}")

    print("=== 2/4: selecting + enriching metrocuadrado candidates ===")
    candidate_urls = [
        listing["url"]
        for listing in metrocuadrado_listings
        if filters.matches(listing, price_field="price")
    ]
    print(
        f"  -> {len(candidate_urls)}/{len(metrocuadrado_listings)} match the filters "
        "(by listed price) and will be enriched"
    )
    if candidate_urls:
        enrich_listings(candidate_urls, headless=headless)

    print(f"=== 3/4: scraping fincaraiz ({fincaraiz_pages} pages) ===")
    fincaraiz_listings = scrape_fincaraiz_listings(max_pages=fincaraiz_pages)
    write_json(FINCARAIZ_LISTINGS_PATH, fincaraiz_listings)
    print(f"  -> {len(fincaraiz_listings)} listings saved to {FINCARAIZ_LISTINGS_PATH.name}")

    print("=== 4/4: merging ===")
    enriched_metrocuadrado = json.loads(LISTINGS_PATH.read_text(encoding="utf-8"))
    metrocuadrado_final = [
        listing
        for listing in enriched_metrocuadrado
        if filters.matches(listing, price_field="total_price")
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
        description="Scrape metrocuadrado + fincaraiz, enrich, and merge listings matching given ranges."
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
    parser.add_argument("--headless", action="store_true", help="Run metrocuadrado's browser headless.")
    return parser.parse_args()


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
    run_pipeline(
        filters,
        metrocuadrado_pages=args.metrocuadrado_pages,
        fincaraiz_pages=args.fincaraiz_pages,
        headless=args.headless,
    )
