"""NSE index constituents, from the CSVs NIFTY Indices publishes.

Source: https://niftyindices.com/IndexConstituent/<file>.csv — static files,
allowed by that host's robots.txt, no auth. Row counts verified 2026-08-06 and
they match the index definitions (Nifty 50 -> 50 rows, Nifty 500 -> 500).

**Weights are not available here.** The spec asked for "index constituents and
weights"; these files carry only Company Name, Industry, Symbol, Series and
ISIN. NSE publishes weights only inside the factsheet PDFs and a paid data
product. Rather than derive a fake weight from free-float market cap we do not
have, this module returns constituents and says weights are unavailable.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass

from indian_markets_mcp.http import Http, UpstreamError, shared

CONSTITUENT_URL = "https://niftyindices.com/IndexConstituent/{slug}.csv"

# Index name -> file slug. Verified reachable and correctly sized on 2026-08-06.
INDICES: dict[str, str] = {
    "NIFTY 50": "ind_nifty50list",
    "NIFTY NEXT 50": "ind_niftynext50list",
    "NIFTY 100": "ind_nifty100list",
    "NIFTY 200": "ind_nifty200list",
    "NIFTY 500": "ind_nifty500list",
    "NIFTY BANK": "ind_niftybanklist",
    "NIFTY IT": "ind_niftyitlist",
    "NIFTY AUTO": "ind_niftyautolist",
    "NIFTY FMCG": "ind_niftyfmcglist",
    "NIFTY PHARMA": "ind_niftypharmalist",
    "NIFTY METAL": "ind_niftymetallist",
    "NIFTY ENERGY": "ind_niftyenergylist",
    "NIFTY REALTY": "ind_niftyrealtylist",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list",
}


@dataclass(frozen=True)
class Constituent:
    company: str
    industry: str
    symbol: str
    series: str
    isin: str


def _normalise(name: str) -> str:
    """Accept 'nifty50', 'Nifty 50', 'NIFTY-50' for the same index."""
    squashed = "".join(ch for ch in name.upper() if ch.isalnum())
    for canonical in INDICES:
        if "".join(ch for ch in canonical if ch.isalnum()) == squashed:
            return canonical
    raise KeyError(name)


def list_indices() -> list[str]:
    return sorted(INDICES)


def constituents(index: str, http: Http | None = None) -> dict:
    try:
        canonical = _normalise(index)
    except KeyError:
        raise UpstreamError(
            f"Unknown index {index!r}. Known indices: {', '.join(sorted(INDICES))}"
        ) from None

    http = http or shared()
    url = CONSTITUENT_URL.format(slug=INDICES[canonical])
    text = http.fetch(url, ttl=24 * 3600).text

    rows = [
        Constituent(
            company=(r.get("Company Name") or "").strip(),
            industry=(r.get("Industry") or "").strip(),
            symbol=(r.get("Symbol") or "").strip(),
            series=(r.get("Series") or "").strip(),
            isin=(r.get("ISIN Code") or "").strip(),
        )
        for r in csv.DictReader(io.StringIO(text))
    ]
    rows = [r for r in rows if r.symbol]
    if not rows:
        raise UpstreamError(f"{url} parsed to zero constituents; the file format may have changed")

    return {
        "index": canonical,
        "count": len(rows),
        "weights_available": False,
        "weights_note": (
            "NIFTY Indices does not publish constituent weights in this free CSV. "
            "Weights appear only in the monthly factsheet PDF and NSE's paid data "
            "product, so this server does not report them."
        ),
        "constituents": [asdict(r) for r in rows],
    }
