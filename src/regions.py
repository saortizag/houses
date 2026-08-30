"""Selectable scrape regions.

Every scraper and pipeline entry point takes a ``region`` (one of the keys in
``REGIONS`` below, default ``"bogota"``). A region is just a bundle of:

* the municipalities it covers (Bogotá is a single municipality; the
  ``chia-cajica`` region covers two, scraped together and merged),
* each municipality's metrocuadrado search URL and fincaraiz search path,
* whether the official cadastral barrio lookup (``barrio_lookup.py``) applies
  — it only exists for Bogotá (that's the only shapefile we have), so it's
  ``False`` for ``chia-cajica`` and ``official_barrio`` is left ``None`` there.

Output for each region goes to its own ``output/<region key>/`` directory
(see ``paths.py``), so switching regions never overwrites another region's
data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Municipality:
    name: str
    # Full metrocuadrado search-results URL (its scraper navigates straight here).
    metrocuadrado_url: str
    # fincaraiz search path, appended to SITE_ROOT; the query string / /paginaN
    # suffix is added by the scraper.
    fincaraiz_path: str


@dataclass(frozen=True)
class Region:
    key: str
    municipalities: tuple[Municipality, ...]
    # Whether official cadastral barrio lookup applies (Bogotá shapefile only).
    barrio_lookup: bool
    # Regex alternation matched against the city portion of a fincaraiz listing
    # title ("... en arriendo en {barrio}, {city}") to pull out {barrio}.
    barrio_city_re: str


BOGOTA = Region(
    key="bogota",
    barrio_lookup=True,
    barrio_city_re=r"bogot\w*",
    municipalities=(
        Municipality(
            name="Bogotá",
            metrocuadrado_url="https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/",
            fincaraiz_path="/arriendo/casas-y-apartamentos/bogota/bogota-dc",
        ),
    ),
)

CHIA_CAJICA = Region(
    key="chia-cajica",
    barrio_lookup=False,
    barrio_city_re=r"ch[ií]a|cajic[aá]",
    municipalities=(
        Municipality(
            name="Chía",
            metrocuadrado_url="https://www.metrocuadrado.com/casa/arriendo/chia/",
            fincaraiz_path="/arriendo/casas-y-cabanas-y-casas-lotes/chia/cundinamarca",
        ),
        Municipality(
            name="Cajicá",
            metrocuadrado_url="https://www.metrocuadrado.com/casa/arriendo/cajica/",
            fincaraiz_path="/arriendo/casas-y-cabanas-y-casas-lotes/cajica/cundinamarca",
        ),
    ),
)

REGIONS: dict[str, Region] = {r.key: r for r in (BOGOTA, CHIA_CAJICA)}
DEFAULT_REGION = BOGOTA.key


def get_region(key: str) -> Region:
    try:
        return REGIONS[key]
    except KeyError:
        raise ValueError(
            f"Unknown region {key!r}; choose one of: {', '.join(REGIONS)}"
        ) from None
