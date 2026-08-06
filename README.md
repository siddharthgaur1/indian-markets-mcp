# indian-markets-mcp

An MCP server exposing Indian market and regulatory data from **official, openly
published sources** — NSE's bhavcopy archive, NIFTY Indices constituent files,
AMFI's industry NAV file, and a local SEBI enforcement-order corpus.

Built against **MCP Python SDK v2** (`mcp >= 2.0.0`).

## Why this one exists

There are already several Indian-market MCP servers (see
[docs/SURVEY.md](docs/SURVEY.md) for the full landscape review). Almost all of
them wrap either `yfinance` or a paid third-party API, and they compete on live
quote coverage.

This server deliberately does not compete there. It covers what the others do
not: **official primary sources with a clean terms position**, plus regulatory
data. Where a source will not permit automated access, this server says so and
omits the tool rather than working around the block.

## What it does and does not cover

| Tool | Source | Status |
|---|---|---|
| `nse_eod_quote` | NSE bhavcopy archive | Working — end-of-day only |
| `nse_eod_history` | NSE bhavcopy archive | Working — capped at 120 days |
| `list_nse_indices` | static list | Working |
| `nse_index_constituents` | niftyindices.com CSV | Working — **no weights** |
| `mf_search_schemes` | AMFI `NAVAll.txt` | Working — ~14,200 schemes |
| `mf_scheme` | AMFI `NAVAll.txt` | Working |
| `mf_nav_history` | mfapi.in | Working |
| `sebi_search_orders` | local corpus | Working — **25-order sample** |

Deliberately **not** implemented, with reasons:

- **Live/intraday quotes.** `nseindia.com/api/quote-equity` returns
  `403 Access Denied` to non-browser clients (verified 2026-08-06). Reaching it
  means forging browser headers to defeat an access control the exchange put
  there on purpose. Not done.
- **Index constituent weights.** NSE publishes weights only in factsheet PDFs
  and a paid data product. The free constituent CSVs carry no weight column, so
  the tool reports `weights_available: false` rather than estimating.
- **Corporate actions (dividends, splits, bonuses).** The spec asked for these.
  No free source was found that serves them to an automated client under terms
  that permit it — NSE's corporate-actions endpoint sits behind the same 403.
  Not implemented rather than half-implemented. See
  [docs/SOURCES.md](docs/SOURCES.md).

## Install

Not yet published to PyPI. From a checkout:

```bash
git clone https://github.com/siddharthgaur1/indian-markets-mcp
cd indian-markets-mcp
python -m venv .venv && .venv/bin/pip install -e .
```

## Claude Desktop configuration

```json
{
  "mcpServers": {
    "indian-markets": {
      "command": "/absolute/path/to/indian-markets-mcp/.venv/bin/indian-markets-mcp"
    }
  }
}
```

On Windows use `.venv\\Scripts\\indian-markets-mcp.exe`.

The SEBI tool additionally needs a corpus; point it at one with:

```json
"env": { "INDIAN_MARKETS_MCP_SEBI_DB": "/path/to/sebi_orders.db" }
```

Every other tool works with **no API keys and no configuration**.

## Behaviour guarantees

- **No fabricated data.** A dead upstream raises a clear error. Missing values
  stay `null`; they are never coerced to zero.
- **Caching.** On-disk SQLite at `~/.cache/indian-markets-mcp/`. Settled
  bhavcopies are cached permanently because they are immutable.
- **Rate limiting.** One-second minimum interval per host, enforced centrally,
  with exponential backoff. Not left to the caller.
- **Stale-cache fallback.** If an upstream is down and a stale cached copy
  exists, it is served rather than failing — and never presented as live.

## Tests

```bash
.venv/bin/pytest            # offline tests only (default)
.venv/bin/pytest -m live    # additionally hits real upstream sources
```

Network-dependent tests are marked `live` and deselected by default, so CI does
not depend on NSE being reachable.

## Licence

MIT. Data served by this tool belongs to its publishers (NSE, NIFTY Indices,
AMFI, SEBI) and is subject to their terms — see [docs/SOURCES.md](docs/SOURCES.md).
