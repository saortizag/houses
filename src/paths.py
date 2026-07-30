"""Shared filesystem locations for all scraper scripts."""

import json
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
CHROME_PROFILE_DIR = PROJECT_ROOT / ".chrome_profile"
INPUT_DIR = PROJECT_ROOT / "input"
SECTOR_SHAPEFILE_PATH = INPUT_DIR / "sector.shp.0425" / "SECTOR.shp"

LISTINGS_PATH = OUTPUT_DIR / "listings.json"
FINCARAIZ_LISTINGS_PATH = OUTPUT_DIR / "fincaraiz_listings.json"
MERGED_LISTINGS_PATH = OUTPUT_DIR / "merged_listings.json"


def write_json(path: Path, data: list[dict]) -> None:
    """Write `data` as JSON to `path`, creating the parent directory (e.g.
    the output/ dir) if it doesn't exist yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
