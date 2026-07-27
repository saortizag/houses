"""Enrich metrocuadrado.com listings.json with per-listing detail-page data.

For each listing URL passed in, visits the detail page (reusing a single
Selenium session) and pulls:
  - Administración / Estrato, from the "hidden sm:block" specs block
  - latitude/longitude and nearby points of interest (name + address), by
    intercepting the "rest-selector-option/selector/locations/points" network
    call the page fires for its map widget

Código isn't scraped from the page at all — it's the last path segment of the
listing's own URL (already known from the first scraping pass), e.g.
".../MC5621754?src_url=..." -> "MC5621754".

Then computes total_price (price + administracion) and price_per_m2
(total_price / area_m2), and writes the enriched entries back into
listings.json in place.
"""

import argparse
import json
import random
import re
import time
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from paths import LISTINGS_PATH, write_json
from stealth_browser import StealthBrowser

NETWORK_ENDPOINT = "rest-selector-option/selector/locations/points"

HIDDEN_SM_BLOCK_SELECTOR = ".hidden.sm\\:block"

ADMINISTRACION_RE = re.compile(r"Administraci[oó]n:\s*\$?\s*([\d.,]+)")
ESTRATO_RE = re.compile(r"Estrato\s+(\d+)")
LATITUDE_RE = re.compile(r"[?&]latitude=([-\d.]+)")
LONGITUDE_RE = re.compile(r"[?&]longitude=([-\d.]+)")


def _parse_cop_number(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _extract_administracion_estrato(driver) -> tuple[int | None, int | None]:
    administracion = None
    estrato = None
    for el in driver.find_elements(By.CSS_SELECTOR, HIDDEN_SM_BLOCK_SELECTOR):
        text = el.text
        if administracion is None:
            match = ADMINISTRACION_RE.search(text)
            if match:
                administracion = _parse_cop_number(match.group(1))
        if estrato is None:
            match = ESTRATO_RE.search(text)
            if match:
                estrato = int(match.group(1))
    return administracion, estrato


def _codigo_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or None


def _extract_location_points(
    browser: StealthBrowser,
) -> tuple[float | None, float | None, list[dict]]:
    request_url, body = browser.find_network_response(NETWORK_ENDPOINT, poll_timeout=12)
    if not request_url:
        return None, None, []

    lat_match = LATITUDE_RE.search(request_url)
    lon_match = LONGITUDE_RE.search(request_url)
    latitude = float(lat_match.group(1)) if lat_match else None
    longitude = float(lon_match.group(1)) if lon_match else None

    points = []
    if isinstance(body, list):
        for item in body:
            points.append({"name": item.get("name"), "address": item.get("address")})

    return latitude, longitude, points


def _settle_page(driver, scroll_steps: int = 8, step_pause: float = 0.5) -> None:
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(0.5)
    for step in range(1, scroll_steps + 1):
        driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight*{step / scroll_steps});")
        time.sleep(step_pause)


def enrich_listing(browser: StealthBrowser, entry: dict) -> dict:
    driver = browser.driver
    browser.get(entry["url"])
    _settle_page(driver)

    administracion, estrato = _extract_administracion_estrato(driver)
    codigo = _codigo_from_url(entry["url"])
    latitude, longitude, points = _extract_location_points(browser)

    entry["administracion"] = administracion
    entry["estrato"] = estrato
    entry["codigo"] = codigo
    entry["latitude"] = latitude
    entry["longitude"] = longitude
    entry["nearby_points"] = points

    price = entry.get("price")
    area_m2 = entry.get("area_m2")
    if price is not None:
        total_price = price + (administracion or 0)
        entry["total_price"] = total_price
        entry["price_per_m2"] = round(total_price / area_m2, 2) if area_m2 else None
    else:
        entry["total_price"] = None
        entry["price_per_m2"] = None

    return entry


def enrich_listings(
    urls: list[str],
    headless: bool = False,
    delay_between_listings: tuple[float, float] = (0.5, 2.0),
) -> list[dict]:
    listings = json.loads(LISTINGS_PATH.read_text(encoding="utf-8"))
    by_url = {item["url"]: item for item in listings}

    missing = [url for url in urls if url not in by_url]
    if missing:
        raise ValueError(f"URLs not found in {LISTINGS_PATH.name}: {missing}")

    enriched = []
    with StealthBrowser(capture_network=True, headless=headless) as browser:
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            enriched.append(enrich_listing(browser, by_url[url]))
            if i < len(urls):
                time.sleep(random.uniform(*delay_between_listings))

    write_json(LISTINGS_PATH, listings)
    print(f"Updated {len(urls)} listings in {LISTINGS_PATH}")
    return enriched


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich metrocuadrado.com listings.json with detail-page data."
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Listing URLs to enrich (must already exist in listings.json). "
        "Defaults to the first 3 listings if omitted.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless (default: headed/visible).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    cli_urls = args.urls
    if not cli_urls:
        sample = json.loads(LISTINGS_PATH.read_text(encoding="utf-8"))[:3]
        cli_urls = [item["url"] for item in sample]
        print(f"No URLs passed on the command line; using first {len(cli_urls)} listings as a sample.")
    enrich_listings(cli_urls, headless=args.headless)
