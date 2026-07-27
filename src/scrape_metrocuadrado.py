"""Scrape apartment/house rental listings from metrocuadrado.com."""

import argparse
import random
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from paths import LISTINGS_PATH, write_json
from stealth_browser import StealthBrowser

BASE_URL = "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/"
RESULTS_CONTAINER = ".property-list__results"
CARD_SELECTOR = (
    f"{RESULTS_CONTAINER} "
    ".property-card__container.property-card__default.property-card__default-normal"
)
CARD_LINK_SELECTOR = ".property-card__content > a"

EXTRACT_CARDS_JS = """
const cards = document.querySelectorAll(arguments[0]);
const out = [];
cards.forEach(card => {
    const a = card.querySelector('.property-card__content > a');
    const priceEl = card.querySelector('.property-card__detail-price');
    const topEl = card.querySelector('.property-card__detail-top');
    const specsEl = card.querySelector('pt-main-specs');
    let area = null, bathrooms = null, parking = null;
    if (specsEl && specsEl.shadowRoot) {
        specsEl.shadowRoot.querySelectorAll('.pt-main-specs--feature pt-text').forEach(f => {
            const text = f.textContent.trim();
            if (text.includes('m²')) area = text;
            else if (text.includes('bañ')) bathrooms = text;
            else if (text.includes('par')) parking = text;
        });
    }
    out.push({
        href: a ? a.href : null,
        price_raw: priceEl ? priceEl.textContent.trim() : null,
        location_raw: topEl ? topEl.textContent.trim() : null,
        area_raw: area,
        bathrooms_raw: bathrooms,
        parking_raw: parking,
    });
});
return out;
"""


def _parse_number(raw: str | None) -> float | None:
    if not raw:
        return None
    match = re.search(r"[\d.,]+", raw)
    if not match:
        return None
    return float(match.group(0).replace(".", "").replace(",", "."))


def _parse_price(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _load_all_cards_on_page(
    driver, max_stable_rounds: int = 2, max_scrolls: int = 20, pause: float = 1.5
) -> int:
    """Scroll down repeatedly until the number of rendered cards stops growing."""
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)

    stable_rounds = 0
    last_count = -1
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        count = len(driver.find_elements(By.CSS_SELECTOR, CARD_SELECTOR))
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= max_stable_rounds:
                break
        else:
            stable_rounds = 0
        last_count = count
    return last_count


def _extract_cards(driver) -> list[dict]:
    raw_cards = driver.execute_script(EXTRACT_CARDS_JS, CARD_SELECTOR)
    listings = []
    for card in raw_cards:
        href = card["href"] or ""
        if "/inmueble/" not in href:
            # Sponsored/ad cards reuse the same card classes but aren't listings.
            continue
        area_raw = card["area_raw"]
        bathrooms_raw = card["bathrooms_raw"]
        parking_raw = card["parking_raw"]
        location_raw = card["location_raw"]
        specs_found = bool(area_raw or bathrooms_raw or parking_raw)

        bathrooms_val = _parse_number(bathrooms_raw)
        parking_val = _parse_number(parking_raw)
        barrio = location_raw.split("|")[0].strip() if location_raw else None

        listings.append(
            {
                "url": card["href"],
                "price": _parse_price(card["price_raw"]),
                "area_m2": _parse_number(area_raw),
                "bathrooms": int(bathrooms_val) if bathrooms_val is not None else None,
                "parking_spots": (
                    int(parking_val) if parking_val is not None else (0 if specs_found else None)
                ),
                "barrio": barrio,
            }
        )
    return listings


def _go_to_next_page(driver, target_page_number: int, wait_seconds: int = 15) -> None:
    first_card = driver.find_element(By.CSS_SELECTOR, CARD_SELECTOR)
    first_href_before = first_card.find_element(By.CSS_SELECTOR, CARD_LINK_SELECTOR).get_attribute(
        "href"
    )

    button = WebDriverWait(driver, wait_seconds).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, f"li.rc-pagination-item-{target_page_number} a")
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    time.sleep(0.3)
    button.click()

    def _href_changed(d):
        try:
            card = d.find_element(By.CSS_SELECTOR, CARD_SELECTOR)
            href = card.find_element(By.CSS_SELECTOR, CARD_LINK_SELECTOR).get_attribute("href")
            return href != first_href_before
        except Exception:
            return False

    WebDriverWait(driver, wait_seconds).until(_href_changed)


def scrape_listings(
    max_pages: int = 5,
    headless: bool = False,
    delay_between_pages: tuple[float, float] = (2.0, 4.0),
) -> list[dict]:
    all_listings = []
    with StealthBrowser(headless=headless) as browser:
        driver = browser.driver
        browser.get(BASE_URL)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, RESULTS_CONTAINER))
        )

        for page_num in range(1, max_pages + 1):
            print(f"Loading page {page_num}/{max_pages}...")
            _load_all_cards_on_page(driver)
            page_listings = _extract_cards(driver)
            print(f"  -> {len(page_listings)} listings found")
            all_listings.extend(page_listings)

            if page_num < max_pages:
                _go_to_next_page(driver, page_num + 1)
                time.sleep(random.uniform(*delay_between_pages))

    return all_listings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape metrocuadrado.com rental search results.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=30,
        help="Number of search-result pages to scrape (default: 30).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless (default: headed/visible).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    listings = scrape_listings(max_pages=args.max_pages, headless=args.headless)
    write_json(LISTINGS_PATH, listings)
    print(f"Saved {len(listings)} listings to {LISTINGS_PATH}")
