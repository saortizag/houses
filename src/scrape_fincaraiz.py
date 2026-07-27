"""Scrape apartment/house rental listings from fincaraiz.com.co.

Unlike metrocuadrado.com, fincaraiz is a server-rendered Next.js app: every
search-results page embeds a full, structured JSON record for each listing
(price breakdown, bathrooms, garage, stratum, exact lat/long, address, ...)
directly in a `<script id="__NEXT_DATA__">` tag in the raw HTML. No browser
or JS execution is needed to see it, and no per-listing detail-page visit is
needed either — so this uses plain HTTP requests instead of StealthBrowser,
and is a single stage (unlike the metrocuadrado card+detail pipeline).
"""

import argparse
import json
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from paths import FINCARAIZ_LISTINGS_PATH, write_json
from stealth_browser import USER_AGENTS

BASE_PATH = "/arriendo/casas-y-apartamentos/bogota/bogota-dc"
QUERY = "ordenListado=3"
SITE_ROOT = "https://www.fincaraiz.com.co"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

BARRIO_RE = re.compile(r"en (?:arriendo|venta) en (.+?),\s*bogot", re.IGNORECASE)


def _page_url(page_num: int) -> str:
    if page_num == 1:
        return f"{SITE_ROOT}{BASE_PATH}?{QUERY}"
    return f"{SITE_ROOT}{BASE_PATH}/pagina{page_num}?{QUERY}"


def _to_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[\d.,]+", str(value))
    if not match:
        return None
    return float(match.group(0).replace(".", "").replace(",", "."))


def _extract_barrio(prop: dict) -> str | None:
    for text in (prop.get("title"), prop.get("description")):
        if not text:
            continue
        match = BARRIO_RE.search(text)
        if match:
            return match.group(1).strip().title()
    return None


def _fetch_search_fast(page_num: int, session: requests.Session) -> dict:
    url = _page_url(page_num)
    response = session.get(url, headers={**HEADERS, "User-Agent": random.choice(USER_AGENTS)})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        raise ValueError(f"__NEXT_DATA__ not found on {url}")

    data = json.loads(script.string)
    return data["props"]["pageProps"]["fetchResult"]["searchFast"]


def _normalize_listing(prop: dict) -> dict:
    price = prop.get("price") or {}
    common_expenses = prop.get("commonExpenses") or {}
    amount = price.get("amount")
    admin = common_expenses.get("amount")

    total_price = price.get("admin_included")
    if total_price is None and amount is not None:
        total_price = amount + (admin or 0)

    area_m2 = _to_number(prop.get("m2Built") or prop.get("m2"))

    return {
        "url": f"{SITE_ROOT}{prop['link']}" if prop.get("link") else None,
        "price": _to_number(amount),
        "area_m2": area_m2,
        "bathrooms": prop.get("bathrooms"),
        "parking_spots": prop.get("garage"),
        "barrio": _extract_barrio(prop),
        "administracion": _to_number(admin),
        "estrato": prop.get("stratum"),
        "codigo": prop.get("code"),
        "latitude": prop.get("latitude"),
        "longitude": prop.get("longitude"),
        "total_price": _to_number(total_price),
        "price_per_m2": round(total_price / area_m2, 2) if total_price and area_m2 else None,
    }


def scrape_listings(
    max_pages: int = 10, delay_between_pages: tuple[float, float] = (1.0, 2.5)
) -> list[dict]:
    all_listings = []
    with requests.Session() as session:
        for page_num in range(1, max_pages + 1):
            search_fast = _fetch_search_fast(page_num, session)
            paginator = search_fast["paginatorInfo"]
            last_page = paginator["lastPage"]

            page_listings = [_normalize_listing(prop) for prop in search_fast["data"]]
            print(
                f"Page {page_num}/{min(max_pages, last_page)} "
                f"(site has {last_page} pages, {paginator['total']} listings total) "
                f"-> {len(page_listings)} listings"
            )
            all_listings.extend(page_listings)

            if page_num >= last_page:
                print("Reached the last available page.")
                break
            if page_num < max_pages:
                time.sleep(random.uniform(*delay_between_pages))

    deduped = list({item["codigo"]: item for item in all_listings}.values())
    if len(deduped) != len(all_listings):
        print(
            f"Removed {len(all_listings) - len(deduped)} duplicate listings "
            "(the site's sort order can shift between page requests as new "
            "listings come in, so consecutive pages can overlap)."
        )

    return deduped


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape fincaraiz.com.co rental search results.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Number of search-result pages to scrape, 21 listings/page (default: 10).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    listings = scrape_listings(max_pages=args.max_pages)
    write_json(FINCARAIZ_LISTINGS_PATH, listings)
    print(f"Saved {len(listings)} listings to {FINCARAIZ_LISTINGS_PATH}")
