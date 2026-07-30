"""Official-barrio (Bogotá neighborhood, per government cadastral SECTOR
shapefile) lookup by point-in-polygon, keyed off each listing's own
latitude/longitude.

Loaded EAGERLY at import time (not lazily) so every thread in
run_pipeline_parallel.py's ThreadPoolExecutor fan-outs sees an
already-built, read-only cache — no lazy-init race, no lock needed. Safe
because both call sites that import this module (scrape_listing_details.py,
scrape_fincaraiz.py) are themselves imported at the top of
run_pipeline_parallel.py, before either of its two ThreadPoolExecutors is
constructed.

Source: input/sector.shp.0425/SECTOR.shp — Bogotá cadastral sectors,
MAGNA-SIRGAS/EPSG:4686 geographic coordinates (numerically ~equivalent to
WGS84/EPSG:4326 for this purpose; no reprojection performed). 1199 records;
SCANOMBRE is the official barrio name (74 names repeat across multiple,
spatially disjoint sector polygons — harmless for a first-match lookup,
since same-named polygons don't overlap each other). encoding="utf-8"
matches the shapefile's own .cpg; pyshp actually auto-detects this from the
sibling .CPG file even without this argument, it's passed explicitly here
for clarity and as a safety net if the .cpg is ever missing.

official_barrio is a separate field from each site's own free-text
"barrio" — they will legitimately disagree often (the cadastral SECTOR
layer is a finer-grained official partition than either site's colloquial
neighborhood naming), that's expected, not a bug.
"""

import json
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import Point, shape
from shapely.prepared import prep

from paths import FINCARAIZ_LISTINGS_PATH, LISTINGS_PATH, SECTOR_SHAPEFILE_PATH, write_json


def _load_sectors() -> list[tuple[str, tuple[float, float, float, float], object]]:
    sectors = []
    with shapefile.Reader(SECTOR_SHAPEFILE_PATH, encoding="utf-8") as sf:
        for shape_record in sf.shapeRecords():
            geometry = shape(shape_record.shape.__geo_interface__)
            name = shape_record.record["SCANOMBRE"].strip()
            sectors.append((name, geometry.bounds, prep(geometry)))
    return sectors


_SECTORS = _load_sectors()  # eager — see module docstring


def lookup_official_barrio(latitude: float | None, longitude: float | None) -> str | None:
    """Official (cadastral) barrio name containing (latitude, longitude), or
    None if either coordinate is missing or the point isn't inside any of
    the 1199 known sectors (expected for listings outside Bogotá proper,
    e.g. La Calera).

    Uses .contains(), not .covers() — a point exactly on a sector boundary
    would miss. Vanishingly rare with float GPS/geocoded coordinates, and a
    miss just yields None here, not a wrong answer, so this is a deliberate
    simplification, not an oversight.
    """
    if latitude is None or longitude is None:
        return None
    x, y = longitude, latitude  # shapely/GeoJSON convention: x=lon, y=lat
    point = Point(x, y)
    for name, (minx, miny, maxx, maxy), prepared_geometry in _SECTORS:
        if not (minx <= x <= maxx and miny <= y <= maxy):
            continue
        if prepared_geometry.contains(point):
            return name
    return None


def enrich_json_file(path: Path) -> tuple[int, int]:
    """Backfill official_barrio onto every entry with coordinates in the
    JSON file at `path`. Entries without lat/long are left untouched (no
    official_barrio key), distinguishing "nothing to check" from "checked,
    no match". Returns (matched_count, checked_count)."""
    listings = json.loads(path.read_text(encoding="utf-8"))
    checked = matched = 0
    for entry in listings:
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if lat is None or lon is None:
            continue
        checked += 1
        official_barrio = lookup_official_barrio(lat, lon)
        entry["official_barrio"] = official_barrio
        if official_barrio is not None:
            matched += 1
    write_json(path, listings)
    return matched, checked


if __name__ == "__main__":
    for target in (LISTINGS_PATH, FINCARAIZ_LISTINGS_PATH):
        if not target.exists():
            print(f"Skipping {target.name} (not found)")
            continue
        matched, checked = enrich_json_file(target)
        print(f"{target.name}: {matched}/{checked} listings with coordinates matched an official barrio")
