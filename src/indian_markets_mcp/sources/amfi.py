"""AMFI mutual fund data.

Source: https://www.amfiindia.com/spages/NAVAll.txt — the daily NAV file AMFI
publishes for the whole industry. No key, no auth, no scraping: it is a static
text file AMFI puts up for exactly this purpose. ~14,000 schemes.

Historical NAV comes from mfapi.in, a free community API that mirrors AMFI's
own historical archive. See docs/SOURCES.md for the terms position on both.

File layout (verified 2026-08-06), which the parser depends on:

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
    <blank>
    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)   <- category
    <blank>
    Aditya Birla Sun Life Mutual Fund                        <- AMC
    <blank>
    119551;INF209KA12Z1;INF209KA13Z9;...;107.0167;05-Aug-2026 <- scheme rows

Category and AMC headers are both bare lines. They are told apart by the
"Schemes(" marker, which every one of the 90 category lines carries and no AMC
name does.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime

from indian_markets_mcp.http import Http, UpstreamError, shared

NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MFAPI_SCHEME_URL = "https://api.mfapi.in/mf/{code}"

_CATEGORY_MARKER = re.compile(r"Schemes?\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class Scheme:
    scheme_code: int
    scheme_name: str
    amc: str
    category: str
    isin_growth: str | None
    isin_reinvestment: str | None
    nav: float | None
    nav_date: str | None


def _clean_isin(raw: str) -> str | None:
    """AMFI writes an absent ISIN as '-'. Return None rather than propagating it."""
    value = raw.strip()
    return None if value in {"", "-", "N.A."} else value


def _parse_nav(raw: str) -> float | None:
    """NAV is blank or 'N.A.' for schemes that did not report. Never coerce to 0.0."""
    value = raw.strip()
    if value in {"", "-", "N.A.", "NA"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    """AMFI publishes '05-Aug-2026'. Normalise to ISO so callers can sort strings."""
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y").date().isoformat()  # noqa: DTZ007 — calendar date, not an instant; tz-aware would be meaningless
    except ValueError:
        return None


def parse_nav_all(text: str) -> list[Scheme]:
    """Parse the whole-industry NAV file into scheme records."""
    schemes: list[Scheme] = []
    category = ""
    amc = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.count(";") == 5:
            code, isin_g, isin_r, name, nav, nav_date = (p.strip() for p in stripped.split(";"))
            if not code.isdigit():  # the header row
                continue
            schemes.append(
                Scheme(
                    scheme_code=int(code),
                    scheme_name=name,
                    amc=amc,
                    category=category,
                    isin_growth=_clean_isin(isin_g),
                    isin_reinvestment=_clean_isin(isin_r),
                    nav=_parse_nav(nav),
                    nav_date=_parse_date(nav_date),
                )
            )
        elif _CATEGORY_MARKER.search(stripped):
            category = stripped
            # `amc` is deliberately NOT reset here. Most category headers are
            # followed by an AMC header, which overwrites this on the next line
            # anyway — but not all of them are. In the file as published on
            # 2026-08-06, "Open Ended Schemes(Growth)" is followed directly by
            # scheme rows with no AMC line, and 24 schemes across the file are
            # in that shape (all legacy plans with NAV dates in 2015-2018).
            # Carrying the previous AMC forward attributes those correctly: the
            # rows in question are ICICI Prudential plans immediately following
            # an ICICI Prudential block. Resetting to "" instead would leave
            # them with a blank AMC, which is a worse answer than the right one.
        else:
            amc = stripped
    return schemes


# The HTTP layer caches the bytes, but re-parsing 1.6 MB into 14k dataclasses
# on every tool call is the actual cost. Memoise the parse against the cache
# entry's timestamp, so a refreshed download reparses and nothing else does.
_parsed: tuple[float, list[Scheme]] | None = None


def load_schemes(http: Http | None = None, ttl: float = 6 * 3600) -> list[Scheme]:
    """Fetch and parse the NAV file. Cached for six hours; AMFI updates daily."""
    global _parsed
    http = http or shared()
    response = http.fetch(NAV_ALL_URL, ttl=ttl)
    if _parsed is not None and _parsed[0] == response.fetched_at:
        return _parsed[1]
    schemes = parse_nav_all(response.text)
    if not schemes:
        # An empty parse means the upstream format moved. Surfacing it as an error
        # is the whole point — a silent empty list would look like "no results".
        raise UpstreamError(
            "AMFI NAVAll.txt parsed to zero schemes; the upstream format has probably changed"
        )
    _parsed = (response.fetched_at, schemes)
    return schemes


def search_schemes(query: str, limit: int = 20, http: Http | None = None) -> list[dict]:
    """Case-insensitive substring search over scheme names, AMC and category."""
    needle = query.strip().lower()
    if not needle:
        return []
    hits = []
    for scheme in load_schemes(http):
        haystack = f"{scheme.scheme_name} {scheme.amc} {scheme.category}".lower()
        if needle in haystack:
            hits.append(scheme)
            if len(hits) >= limit:
                break
    return [asdict(s) for s in hits]


def get_scheme(scheme_code: int, http: Http | None = None) -> dict | None:
    for scheme in load_schemes(http):
        if scheme.scheme_code == scheme_code:
            return asdict(scheme)
    return None


def nav_history(
    scheme_code: int,
    start: date | None = None,
    end: date | None = None,
    http: Http | None = None,
) -> dict:
    """Historical NAV series for one scheme, from mfapi.in."""
    http = http or shared()
    response = http.fetch(MFAPI_SCHEME_URL.format(code=scheme_code), ttl=6 * 3600)
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise UpstreamError(f"mfapi.in returned non-JSON for scheme {scheme_code}") from exc

    if payload.get("status") != "SUCCESS" or not payload.get("data"):
        raise UpstreamError(
            f"mfapi.in has no NAV history for scheme {scheme_code} "
            f"(status={payload.get('status')!r})"
        )

    series = []
    for row in payload["data"]:
        try:
            day = datetime.strptime(row["date"], "%d-%m-%Y").date()  # noqa: DTZ007 — calendar date, not an instant; tz-aware would be meaningless
        except (KeyError, ValueError):
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        series.append({"date": day.isoformat(), "nav": float(row["nav"])})

    series.sort(key=lambda r: r["date"])
    return {
        "scheme_code": scheme_code,
        "meta": payload.get("meta", {}),
        "count": len(series),
        "series": series,
    }
