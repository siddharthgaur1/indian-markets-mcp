"""MCP server exposing Indian market and regulatory data.

Built against MCP Python SDK v2 (`mcp.server.mcpserver.MCPServer`). Note that
v2 removed `mcp.server.fastmcp` entirely — the `FastMCP` class most examples
still show does not exist in the current SDK.

Tool descriptions are the interface the model sees, so they carry the coverage
and provenance caveats, not just the parameter list. A model that cannot tell a
25-order sample from SEBI's full archive will make claims the data cannot
support.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from indian_markets_mcp.http import UpstreamError
from indian_markets_mcp.sources import amfi, indices, nse_archives, sebi

mcp = MCPServer("indian-markets")

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{label} must be an ISO date like 2026-08-05, got {value!r}") from None


# --------------------------------------------------------------------------
# Equities — end-of-day, from NSE's public bhavcopy archive
# --------------------------------------------------------------------------


@mcp.tool(
    title="NSE end-of-day quote",
    description=(
        "End-of-day OHLCV for an NSE-listed stock, from NSE's official published "
        "bhavcopy archive. This is SETTLED END-OF-DAY data, never a live intraday "
        "price — do not present it as the current price. Omit trade_date for the "
        "most recent published trading day. Returns open/high/low/close, previous "
        "close, volume, turnover and trade count."
    ),
    annotations=READ_ONLY,
)
def nse_eod_quote(
    symbol: Annotated[str, Field(description="NSE ticker, e.g. RELIANCE, TCS, INFY")],
    trade_date: Annotated[
        str | None, Field(description="ISO date YYYY-MM-DD. Omit for the latest trading day.")
    ] = None,
) -> dict:
    return nse_archives.eod_quote(symbol, _parse_day(trade_date, "trade_date"))


@mcp.tool(
    title="NSE end-of-day history",
    description=(
        "Daily OHLCV series for one NSE symbol over a date range. Each day is a "
        "separate archive file, so the range is capped at 120 calendar days; for "
        "multi-year history use a pre-built warehouse instead. Non-trading days are "
        "absent from the series and counted in days_unavailable."
    ),
    annotations=READ_ONLY,
)
def nse_eod_history(
    symbol: Annotated[str, Field(description="NSE ticker, e.g. RELIANCE")],
    start: Annotated[str, Field(description="ISO start date YYYY-MM-DD, inclusive")],
    end: Annotated[str, Field(description="ISO end date YYYY-MM-DD, inclusive")],
) -> dict:
    start_day = _parse_day(start, "start")
    end_day = _parse_day(end, "end")
    assert start_day and end_day
    return nse_archives.eod_history(symbol, start_day, end_day)


# --------------------------------------------------------------------------
# Indices
# --------------------------------------------------------------------------


@mcp.tool(
    title="List NSE indices",
    description="Names of the NSE/NIFTY indices whose constituents this server can return.",
    annotations=READ_ONLY,
)
def list_nse_indices() -> dict:
    return {"indices": indices.list_indices()}


@mcp.tool(
    title="NSE index constituents",
    description=(
        "Constituent stocks of an NSE index (e.g. 'NIFTY 50', 'NIFTY BANK'), from the "
        "CSV that NIFTY Indices publishes. Returns company name, industry, symbol, "
        "series and ISIN. CONSTITUENT WEIGHTS ARE NOT AVAILABLE — NSE publishes them "
        "only in factsheet PDFs and a paid product, so do not infer or estimate them."
    ),
    annotations=READ_ONLY,
)
def nse_index_constituents(
    index: Annotated[str, Field(description="Index name, e.g. 'NIFTY 50', 'NIFTY IT'")],
) -> dict:
    return indices.constituents(index)


# --------------------------------------------------------------------------
# Mutual funds — AMFI
# --------------------------------------------------------------------------


@mcp.tool(
    title="Search mutual fund schemes",
    description=(
        "Search all ~14,000 Indian mutual fund schemes by name, AMC or category, from "
        "AMFI's daily industry NAV file. Returns scheme code, name, AMC, category, "
        "ISINs and latest NAV. Use the returned scheme_code with mf_nav_history. "
        "A scheme's NAV may be null when it did not report that day."
    ),
    annotations=READ_ONLY,
)
def mf_search_schemes(
    query: Annotated[str, Field(description="Substring of scheme name, AMC or category")],
    limit: Annotated[int, Field(description="Max results, 1-100", ge=1, le=100)] = 20,
) -> dict:
    results = amfi.search_schemes(query, limit)
    return {"count": len(results), "schemes": results}


@mcp.tool(
    title="Mutual fund scheme detail",
    description="Latest NAV and metadata for one AMFI scheme code.",
    annotations=READ_ONLY,
)
def mf_scheme(
    scheme_code: Annotated[int, Field(description="AMFI scheme code, e.g. 119551")],
) -> dict:
    found = amfi.get_scheme(scheme_code)
    if found is None:
        raise UpstreamError(
            f"Scheme code {scheme_code} is not in AMFI's current NAV file. It may be "
            "a matured or merged scheme."
        )
    return found


@mcp.tool(
    title="Mutual fund NAV history",
    description=(
        "Historical NAV series for one scheme, oldest first. Optionally bounded by "
        "start/end ISO dates. Sourced from mfapi.in, which mirrors AMFI's historical "
        "NAV archive."
    ),
    annotations=READ_ONLY,
)
def mf_nav_history(
    scheme_code: Annotated[int, Field(description="AMFI scheme code, e.g. 119551")],
    start: Annotated[str | None, Field(description="ISO start date, inclusive")] = None,
    end: Annotated[str | None, Field(description="ISO end date, inclusive")] = None,
) -> dict:
    return amfi.nav_history(scheme_code, _parse_day(start, "start"), _parse_day(end, "end"))


# --------------------------------------------------------------------------
# SEBI enforcement orders
# --------------------------------------------------------------------------


@mcp.tool(
    title="Search SEBI enforcement orders",
    description=(
        "Search a LOCAL SAMPLE of SEBI enforcement orders by text, entity or year. "
        "IMPORTANT: this corpus is a small sample (see corpus_size in the response), "
        "NOT SEBI's complete enforcement archive. If a search returns nothing, say "
        "that the sample contains no match — never conclude that SEBI has issued no "
        "order against the entity."
    ),
    annotations=READ_ONLY,
)
def sebi_search_orders(
    query: Annotated[str, Field(description="Free text over title, entity, violation type")] = "",
    entity: Annotated[str | None, Field(description="Filter by entity name")] = None,
    year: Annotated[int | None, Field(description="Filter by order year, e.g. 2026")] = None,
    limit: Annotated[int, Field(description="Max results, 1-100", ge=1, le=100)] = 20,
) -> dict:
    return sebi.search_orders(query=query, entity=entity, year=year, limit=limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
