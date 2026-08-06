# Phase 0 — landscape survey

Run 2026-08-06. The question this phase had to answer was *should we build this
at all*, and the honest answer changed the shape of the project.

## What already exists

Indian-market MCP servers are **not** a gap. Found on GitHub, npm, mcp.so,
mcpmarket, glama and lobehub:

| Project | Data source | Notes |
|---|---|---|
| `Tapetide-hq/nse-bse-indian-stock-market-data-mcp` | commercial backend | 34 tools, ~8,200 stocks, screener, FII/DII flows |
| `bshada/nse-bse-mcp` | NSE/BSE APIs | 66 tools, Streamable HTTP, on npm |
| `GirishKumarDV/Live-NSE-BSE-MCP` | IndianAPI (paid) | 14 tools |
| `anuragkrishna/Indian-Stock-Exchange-MCP` | Indian Stock Market API (paid) | company financials |
| `parthashirolkar/stock-analysis-mcp` | yfinance | FastMCP |
| `neerajadhav/kai-stock-market-mcp` | yfinance | NSE-optimised, charting |
| `hi-imcodeman/stock-nse-india` | NSE endpoints | npm library with an MCP server |
| mftool-mcp / mfapi-india | AMFI | mutual funds specifically |

**Verdict: do not build another live-quote server.** That niche is saturated and
the incumbents are further along than two weeks of work would get.

Two observations that did leave room:

1. **Almost every one of them wraps either `yfinance` or a paid third-party
   API.** Very few go to the primary official source. That matters because
   `yfinance` for Indian equities is itself a scrape of Yahoo, with its own
   terms problem one layer down, and the paid wrappers make the server useless
   without someone else's key.
2. **Regulatory data is genuinely absent.** The only SEBI-adjacent MCP server
   found was `53rao/SEBI-Research-Analyst-Compliance-MCP`, which serves the text
   of the Research Analyst Regulations from a JSON file — a different thing from
   enforcement orders. Nothing covers SEBI enforcement actions.

## What that changed

Scope moved from "quotes and history" to **official primary sources plus
regulatory data**, and the live-quote tools were dropped entirely — reinforced
by the finding that NSE's quote API blocks automated clients anyway (see
[SOURCES.md](SOURCES.md)).

Contributing to an existing server was considered and rejected: the incumbents
are built around the commercial APIs whose coverage this project deliberately
avoids, so the useful work does not compose with theirs.

## Protocol check

The spec required checking the current MCP specification rather than trusting
training data. This mattered more than expected.

- Current spec revision: **2026-07-28**.
- Current Python SDK: **`mcp` 2.0.0**, released 2026-07-28 — eight days before
  this survey.
- **`mcp.server.fastmcp` no longer exists.** v2 is a major rework. The `FastMCP`
  class that nearly every tutorial and every pre-2026 example uses was renamed
  to `MCPServer`, at `mcp.server.mcpserver` (also re-exported from `mcp.server`).

Verified directly against the installed package:

```
$ python -c "import mcp.server.fastmcp"
ModuleNotFoundError: No module named 'mcp.server.fastmcp'

$ python -c "from mcp.server.mcpserver import MCPServer; print(MCPServer)"
<class 'mcp.server.mcpserver.server.MCPServer'>
```

Writing this server from training memory would have produced a file that does
not import. Current idioms adopted here:

- `@mcp.tool(title=..., description=..., annotations=ToolAnnotations(...))`
- `ToolAnnotations(read_only_hint=True, idempotent_hint=True)` on every tool —
  all eight are reads
- `Annotated[T, Field(description=...)]` for per-parameter schema descriptions
- Return `dict` / Pydantic models; structured output is derived automatically
