# Bogotá Rental Listings Scrapers

Scrapes apartment/house rental listings for Bogotá from two sites:

- [metrocuadrado.com](https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/)
  — a two-stage, Selenium-driven pipeline (see below for why).
- [fincaraiz.com.co](https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota/bogota-dc)
  — a single-stage, plain-HTTP pipeline (see below for why).

They're independent: separate scripts, separate output files
(`output/listings.json` vs `output/fincaraiz_listings.json`). The two output
schemas share the same field names wherever the data is conceptually the
same (`price`, `area_m2`, `bathrooms`, `parking_spots`, `barrio`,
`administracion`, `estrato`, `codigo`, `latitude`, `longitude`,
`total_price`, `price_per_m2`), so both files can be loaded and compared the
same way (see `analyze.ipynb`).

`run_pipeline.py` drives both of them together and merges the results that
match a set of price/area/bathrooms/parking-spot ranges you choose — see
[Combined pipeline](#combined-pipeline-run_pipelinepy) below.
`run_pipeline_parallel.py` does the same thing faster, by overlapping the
independent parts instead of running everything sequentially — see
[Parallel pipeline](#parallel-pipeline-run_pipeline_parallelpy).

All code lives in `src/`; all commands below are meant to be run from the
**project root** (not from inside `src/`). All generated data is written to
`output/`, which is created automatically the first time any script runs —
you don't need to create it yourself.

## Requirements

- The `cars` conda environment (`python 3.14`) — run everything with
  `conda run -n cars python ...` or `conda activate cars` first.
- **metrocuadrado only:** `selenium 4.43` + Chromium installed at
  `/snap/bin/chromium` (default; override via
  `StealthBrowser(binary_location=...)`). Runs headed (a visible browser
  window) by default — needs a display. Pass `--headless` (or
  `headless=True`) to run without one.
- **fincaraiz only:** `requests` + `beautifulsoup4` (both already in `cars`).
  No browser, no display needed — see why in its section below.

## Project structure

```
houses/
├── src/
│   ├── paths.py                   # shared path constants + write_json() helper
│   ├── stealth_browser.py
│   ├── scrape_metrocuadrado.py
│   ├── scrape_listing_details.py
│   ├── scrape_fincaraiz.py
│   ├── run_pipeline.py            # sequential pipeline
│   └── run_pipeline_parallel.py   # same pipeline, overlapped/concurrent
├── output/                        # created automatically; all generated data
│   ├── listings.json
│   ├── fincaraiz_listings.json
│   └── merged_listings.json
├── .chrome_profile/                # StealthBrowser's Chrome profile (gitignored)
├── analyze.ipynb
└── README.md
```

| File | Purpose |
|---|---|
| `src/paths.py` | Single source of truth for every path below (`OUTPUT_DIR`, `LISTINGS_PATH`, etc.) plus `write_json()`, which creates `output/` on first write if it doesn't exist yet. Every other script imports from here instead of computing its own paths. |
| `src/stealth_browser.py` | `StealthBrowser` class — the reusable Selenium browser, used by the metrocuadrado scripts. |
| `src/scrape_metrocuadrado.py` | metrocuadrado stage 1: scrapes the search-results grid into `output/listings.json`. |
| `src/scrape_listing_details.py` | metrocuadrado stage 2: enriches specific listings with detail-page data. |
| `output/listings.json` | metrocuadrado output, shared by both of its stages. |
| `src/scrape_fincaraiz.py` | fincaraiz: single-stage scrape straight into `output/fincaraiz_listings.json`. |
| `output/fincaraiz_listings.json` | fincaraiz output. |
| `src/run_pipeline.py` | Runs both sites end-to-end, sequentially, and merges the results matching your filters into `output/merged_listings.json`. |
| `src/run_pipeline_parallel.py` | Same as above, but scrapes both sites concurrently and fans metrocuadrado's enrichment out across multiple browsers at once. |
| `output/merged_listings.json` | Combined output of either pipeline script. |
| `analyze.ipynb` | Example notebook loading a `output/*.json` file into pandas for a quick look. |

All the scripts in `src/` are siblings that import each other directly
(`from stealth_browser import StealthBrowser`, `from paths import ...`, etc.)
— there's no `__init__.py`/package structure. This works for the `conda run
-n cars python src/script.py` form because Python puts a script's own
directory on `sys.path` automatically. For ad-hoc Python usage (a `python -c`
one-liner, a notebook cell) from the project root, add `src` to the path
first: `sys.path.insert(0, "src")` — every Python snippet below does this.

## metrocuadrado.com

metrocuadrado is a heavy client-side app (specs render inside a Shadow DOM
web component, pagination is a JS click handler with no real `href`, and
some data — the listing's precise coordinates and nearby points of interest
— only ever appears via an internal API call the page makes for its map
widget). None of that is visible in the raw HTML, so this pipeline drives an
actual browser and reads the rendered/live page. It's two stages because the
detail page has fields (Administración, Estrato) and that map API call
aren't present on the search-results cards at all.

### `stealth_browser.py`

`StealthBrowser` launches Chromium with a realistic user-agent/viewport,
disabled automation flags, and CDP-level patches (`navigator.webdriver`,
`languages`, `plugins`, `permissions.query`) so the page doesn't see an
obvious automated browser. It keeps its Chrome profile in a project-local
`.chrome_profile/` directory — required because the snap-confined Chromium
build can't reliably use a default temp profile path.

It's a context manager:

```python
import sys
sys.path.insert(0, "src")
from stealth_browser import StealthBrowser

with StealthBrowser() as browser:
    browser.get("https://example.com")
    browser.driver  # the underlying selenium webdriver.Chrome
```

Pass `capture_network=True` to also capture network traffic (via Chrome's
performance log + CDP), then use `browser.find_network_response(url_substring)`
to find a specific API call the page made and get back its request URL and
parsed JSON response body. This is how stage 2 pulls the listing's
coordinates and nearby points of interest without them ever appearing in the
page's HTML.

Pass `headless=True` to run without a visible window (uses Chrome's `--headless=new`
mode, which is harder to fingerprint than the legacy headless mode). Both
metrocuadrado scripts expose this as a `--headless` CLI flag.

### Stage 1 — scrape search results

```bash
conda run -n cars python src/scrape_metrocuadrado.py [--max-pages N] [--headless]
```

Pages through the search results (clicking the site's pagination — it's
client-side, so URL query params like `?page=2` don't work) and scroll-loads
each page's lazily-rendered cards. Writes/overwrites `output/listings.json`
(creating `output/` first if needed).

**This overwrites `output/listings.json` completely**, including any
detail-page enrichment from stage 2. If you've already enriched listings and
want to keep that data, back up `output/listings.json` before re-running
stage 1.

| Flag | Default | Meaning |
|---|---|---|
| `--max-pages N` | `30` | Number of search-result pages to scrape (~60 listings/page). The search has ~200 pages (13,000+ listings total), so this is a deliberate cap, not the full site. |
| `--headless` | off (headed) | Run without a visible browser window. |

Same options are available calling it directly from Python:

```python
import sys
sys.path.insert(0, "src")
from scrape_metrocuadrado import scrape_listings
from paths import LISTINGS_PATH, write_json

listings = scrape_listings(max_pages=20, headless=True)
write_json(LISTINGS_PATH, listings)
```

Sponsored/ad cards that appear inline in the results grid (sharing the same
CSS classes as real listing cards) are filtered out automatically.

### Stage 2 — enrich specific listings

```bash
conda run -n cars python src/scrape_listing_details.py [--headless] <url1> <url2> ...
# no urls -> enriches the first 3 listings in output/listings.json as a sample
```

Or from Python, to enrich an arbitrary subset:

```python
import sys
sys.path.insert(0, "src")
from scrape_listing_details import enrich_listings

urls = [item["url"] for item in listings[:50]]  # any subset you want
enrich_listings(urls, headless=True)
```

All URLs are visited in a **single browser session**. Each URL must already
exist as an entry in `output/listings.json` (i.e. come from stage 1) — the
enriched fields are merged into that entry in place and the whole file is
rewritten.

### Output schema (`output/listings.json`)

Every entry has these fields after stage 1:

| Field | Type | Notes |
|---|---|---|
| `url` | string | Listing detail page URL. |
| `price` | int \| null | Monthly rent, COP. |
| `area_m2` | float \| null | Built area, m². |
| `bathrooms` | int \| null | |
| `parking_spots` | int \| null | Defaults to `0` when the specs block is present but shows no parking feature. |
| `barrio` | string \| null | Neighborhood, parsed from the card's location line. |

Stage 2 adds these fields **only to the URLs you pass in**:

| Field | Type | Notes |
|---|---|---|
| `administracion` | int \| null | Monthly HOA/admin fee, COP. Often absent for houses. |
| `estrato` | int \| null | Colombian socioeconomic stratum (1–6). |
| `codigo` | string \| null | Listing code, parsed from the URL itself (last path segment before the query string) — not scraped from the page. |
| `latitude` / `longitude` | float \| null | The listing's coordinates, read from the map widget's own network request. |
| `nearby_points` | list of `{name, address}` | Points of interest near the listing, from the same network request. |
| `total_price` | int \| null | `price + (administracion or 0)`. |
| `price_per_m2` | float \| null | `total_price / area_m2`, rounded to 2 decimals. |

## fincaraiz.com.co

fincaraiz is a server-rendered Next.js app: every search-results page embeds
a full, structured JSON record for **every** listing on that page — price
breakdown, bathrooms, garage, stratum, exact latitude/longitude, address,
even the same technical-sheet data shown on the individual listing page —
directly in a `<script id="__NEXT_DATA__">` tag in the raw HTML. No
JavaScript execution is needed to see it, and visiting each listing's own
detail page adds nothing that isn't already on the search-results page. So
unlike metrocuadrado, this is:

- **one stage**, not two — `scrape_fincaraiz.py` is both the list scrape and
  the "enrichment," since everything comes from the same page fetch.
- **plain HTTP via `requests`**, not Selenium/`StealthBrowser` — there's no
  client-side rendering to wait for, so a real browser buys nothing here.
  A realistic User-Agent (reused from `stealth_browser.USER_AGENTS`) and
  Accept-Language header are still set by hand on every request.

### Usage

```bash
conda run -n cars python src/scrape_fincaraiz.py [--max-pages N]
```

Pages through `/pagina2`, `/pagina3`, ... (real URLs, unlike metrocuadrado's
client-side pagination) and pulls each page's listings straight out of its
embedded JSON. Writes/overwrites `output/fincaraiz_listings.json` (creating
`output/` first if needed).

**This overwrites `output/fincaraiz_listings.json` completely** — back it up
first if you want to keep a previous run's data (e.g. before testing with a
small `--max-pages` value).

| Flag | Default | Meaning |
|---|---|---|
| `--max-pages N` | `10` | Number of search-result pages to scrape (21 listings/page). The search has ~400 pages (8,500+ listings total), so this is a deliberate cap. |

Same option from Python:

```python
import sys
sys.path.insert(0, "src")
from scrape_fincaraiz import scrape_listings
from paths import FINCARAIZ_LISTINGS_PATH, write_json

listings = scrape_listings(max_pages=30)
write_json(FINCARAIZ_LISTINGS_PATH, listings)
```

The site's result ordering can shift slightly between one page request and
the next as new listings come in live, which occasionally puts the same
listing on two consecutive page fetches — `scrape_listings()` dedupes the
final list by `codigo` before returning/writing, logging how many duplicates
it dropped.

### Output schema (`output/fincaraiz_listings.json`)

All fields are populated in a single pass (no separate enrichment step):

| Field | Type | Notes |
|---|---|---|
| `url` | string | Listing detail page URL. |
| `price` | float \| null | Base monthly rent, COP (excludes admin fee unless the listing bundles it — see `total_price`). |
| `area_m2` | float \| null | Built area, m². |
| `bathrooms` | int \| null | |
| `parking_spots` | int \| null | `0` means no garage, straight from the listing's own `garage` field. |
| `barrio` | string \| null | Parsed from the listing title (`"... en {barrio}, Bogotá"`). Null for listings whose title doesn't follow that pattern (e.g. properties in a neighboring municipality like La Calera, or where the site itself doesn't disclose a specific neighborhood) — about 3% of listings in practice. |
| `administracion` | float \| null | Monthly HOA/admin fee, COP. `0` if none or already bundled into `price`. |
| `estrato` | int \| null | Colombian socioeconomic stratum (1–6). |
| `codigo` | string \| null | fincaraiz's own listing code (also the last URL path segment). |
| `latitude` / `longitude` | float \| null | The listing's coordinates, straight from its record. |
| `total_price` | float \| null | The site's own admin-inclusive total if present, else `price + administracion`. |
| `price_per_m2` | float \| null | `total_price / area_m2`, rounded to 2 decimals. |

There's no `nearby_points` equivalent here — fincaraiz's listing data doesn't
expose a nearby-points-of-interest feature the way metrocuadrado's map
widget does.

## Combined pipeline (`run_pipeline.py`)

Runs both sites end-to-end and merges whatever matches a set of optional
price/area/bathrooms/parking-spot ranges into one `output/merged_listings.json`,
with a `source` field (`"metrocuadrado"` or `"fincaraiz"`) added to each
entry:

```bash
conda run -n cars python src/run_pipeline.py \
  --max-price 3000000 --min-bathrooms 2 \
  --metrocuadrado-pages 30 --fincaraiz-pages 10 [--headless]
```

| Flag | Meaning |
|---|---|
| `--min-price` / `--max-price` | COP range. |
| `--min-area` / `--max-area` | m² range. |
| `--min-bathrooms` / `--max-bathrooms` | |
| `--min-parking` / `--max-parking` | |
| `--metrocuadrado-pages` | Default `30`, passed straight to `scrape_metrocuadrado.py`. |
| `--fincaraiz-pages` | Default `10`, passed straight to `scrape_fincaraiz.py`. |
| `--headless` | Applies to metrocuadrado's browser only (fincaraiz never uses one). |

All range flags are optional and independent — omit any you don't want to
constrain. Same from Python:

```python
import sys
sys.path.insert(0, "src")
from run_pipeline import run_pipeline, ListingFilters

filters = ListingFilters(max_price=3_000_000, min_bathrooms=2)
merged = run_pipeline(filters, metrocuadrado_pages=30, fincaraiz_pages=10)
```

What it does, in order:

1. Scrapes metrocuadrado search results (→ `output/listings.json`, same as
   running `scrape_metrocuadrado.py` directly).
2. Selects the metrocuadrado listings matching your ranges and enriches just
   that subset (→ same `output/listings.json`, updated in place). This
   selection is checked against the raw `price` field, since `total_price`
   doesn't exist until after enrichment adds `administracion`.
3. Scrapes fincaraiz search results (→ `output/fincaraiz_listings.json`);
   every listing is already fully enriched by construction (see its section
   above), no separate step needed.
4. Re-filters the now-enriched metrocuadrado listings by the same ranges —
   this time checked against `total_price`, now that it's known — and
   filters fincaraiz listings by the same ranges (also against
   `total_price`, which it has natively). Concatenates both into
   `output/merged_listings.json`.

Because of that price-field switch, a metrocuadrado listing can pass step 2's
selection (cheap enough by listed price) but still miss the final merge if
its administración fee pushes `total_price` over `--max-price` — that's
expected, not a bug: it gets enriched (and stays in `output/listings.json`
with the enrichment data) but doesn't make it into the merged output for
this run.

**Steps 1 and 3 fully overwrite `output/listings.json` and
`output/fincaraiz_listings.json`** each run, same as calling those scripts
directly — back them up first if you want to keep a previous run's data
(e.g. before a quick test with small page counts).

## Parallel pipeline (`run_pipeline_parallel.py`)

Same end result as `run_pipeline.py` above, but overlaps the independent
parts of the pipeline instead of running everything one step at a time.
`run_pipeline.py` itself is untouched — this is a separate script, not a
replacement, and the two produce compatible output (same schema, same
`output/*.json` files) so either can be used interchangeably depending on
whether you want the simpler sequential version or the faster parallel one.

What runs concurrently:

1. **Metrocuadrado's stage-1 scrape runs at the same time as fincaraiz's
   scrape** — different sites, different output files, no shared state, so
   there's no reason to wait for one before starting the other.
2. **Once metrocuadrado stage 1 finishes**, candidates are selected (same
   filter-by-raw-`price` logic as `run_pipeline.py`'s step 2) and split into
   chunks of `--chunk-size` listings (default 25). Each chunk is enriched by
   its **own independent browser**, running concurrently, capped at
   `--max-concurrent-browsers` at once (default 4) — extra chunks queue and
   start as earlier ones finish, rather than launching one browser per chunk
   unconditionally. This cap exists because concurrency here isn't free: more
   simultaneous browsers means more RAM/CPU and a higher chance of tripping
   metrocuadrado's rate-limiting than today's single sequential session.

```bash
conda run -n cars python src/run_pipeline_parallel.py \
  --max-price 3000000 --min-bathrooms 2 \
  --metrocuadrado-pages 30 --fincaraiz-pages 10 \
  --chunk-size 25 --max-concurrent-browsers 4 [--headless] [--no-enrich-headless]
```

Same filter flags as `run_pipeline.py` (`--min-price` ... `--max-parking`,
`--metrocuadrado-pages`, `--fincaraiz-pages`), plus:

| Flag | Default | Meaning |
|---|---|---|
| `--headless` | off (headed) | Metrocuadrado stage-1's browser — same name/default as `run_pipeline.py`. |
| `--enrich-headless` / `--no-enrich-headless` | **on** (headless) | Enrichment fan-out browsers. Defaults to headless since several simultaneous visible windows is impractical; pass `--no-enrich-headless` to watch them. |
| `--chunk-size` | `25` | Candidates per enrichment browser session. |
| `--max-concurrent-browsers` | `4` | Cap on simultaneous enrichment browsers. |

Same from Python:

```python
import sys
sys.path.insert(0, "src")
from run_pipeline_parallel import run_pipeline_parallel
from run_pipeline import ListingFilters

filters = ListingFilters(max_price=3_000_000, min_bathrooms=2)
merged = run_pipeline_parallel(filters, metrocuadrado_pages=30, fincaraiz_pages=10)
```

Each enrichment chunk gets its own Chrome profile directory (a numbered
subdirectory of `.chrome_profile/`, e.g. `.chrome_profile/chunk_002/`), so
concurrent browser instances never collide on the same profile lock. If a
single listing fails to enrich, only that listing is skipped (logged, not
fatal) — the rest of its chunk continues; if a whole chunk fails
catastrophically (e.g. its browser crashes), only that chunk's listings are
affected, the others keep going. A run's console output is prefixed per
chunk (`[chunk 0]`, `[chunk 1]`, ...) since multiple chunks print
concurrently.

Same overwrite behavior and caveats as `run_pipeline.py`: **fully overwrites
`output/listings.json` and `output/fincaraiz_listings.json`** each run —
back them up first if you want to keep previous data.

## Notes

- Both `scrape_metrocuadrado.py` and `scrape_listing_details.py` add small
  randomized delays between page/listing visits, and `scrape_fincaraiz.py`
  does the same between page requests, to behave less like a bot; expect a
  multi-page or multi-URL run to take a while by design.
- Scraping either site's full result set in one run (13,000+ listings on
  metrocuadrado, 8,500+ on fincaraiz) is possible but slow and more likely to
  get rate-limited — the `max_pages` defaults and the URL-subset design for
  metrocuadrado's stage 2 are there so you can scale up deliberately rather
  than by default.
