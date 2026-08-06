# Phase 1 — data sources, terms and rate limits

Every source was probed on 2026-08-06. Status below is what the source actually
returned, not what its documentation claims.

## Summary

| Source | Endpoint | Status | robots.txt | Used |
|---|---|---|---|---|
| AMFI | `amfiindia.com/spages/NAVAll.txt` | 200, 1.6 MB | permits | yes |
| mfapi.in | `api.mfapi.in/mf/<code>` | 200 | permits | yes |
| NIFTY Indices | `niftyindices.com/IndexConstituent/*.csv` | 200 | permits | yes |
| NSE archives | `nsearchives.nseindia.com/content/cm/BhavCopy_*.zip` | 200 | permits | yes |
| **NSE live quote** | `nseindia.com/api/quote-equity` | **403 Access Denied** | permits | **no** |
| SEBI | order pages | n/a — local corpus | permits | indirectly |

## The one that decided the project's scope

```
GET https://www.nseindia.com/api/quote-equity?symbol=RELIANCE
-> 403 Access Denied
   "You don't have permission to access ... on this server."
```

NSE's `robots.txt` is permissive (`Allow: /`, disallowing only
`/market-data-test`), so this is not a robots decision — it is an active
edge-level block on non-browser clients.

Getting through it is well documented and entirely mechanical: send a browser
`User-Agent`, fetch the homepage first to pick up the cookies, replay them. That
is not a technical obstacle, it is an access control, and defeating it is the
thing the spec explicitly said would make this server a liability rather than a
portfolio piece. **Not implemented.**

The consequence is that this server has no live-quote tool. End-of-day data
comes from the archive instead, which NSE serves to a plain client without
complaint.

## What is served, and under what terms

### AMFI — `NAVAll.txt`
A static text file AMFI publishes daily for the entire industry, ~14,200
schemes, no key and no session. Publishing it in this form is the point of the
file. Format verified: header row, then repeating
category / AMC / scheme-row blocks; 90 categories present.

Cached 6 hours (AMFI updates once a day).

### mfapi.in
A free community API mirroring AMFI's historical NAV archive. No key, no
sign-up. Used only for historical series, since `NAVAll.txt` carries just the
latest NAV. It is a third party rather than the primary source, and the README
says so.

Cached 6 hours.

### NIFTY Indices — constituent CSVs
Static CSVs, allowed by `niftyindices.com/robots.txt`. Row counts verified
against the index definitions (Nifty 50 → 50 rows, Nifty 500 → 500, Nifty Bank
→ 14).

**Weights are not published here.** The columns are Company Name, Industry,
Symbol, Series, ISIN — nothing else. NSE puts weights in the monthly factsheet
PDFs and in a paid data product. The tool reports `weights_available: false`
rather than deriving a plausible-looking number from free-float market cap it
does not have.

**Operational note, learned the hard way.** This host's WAF black-holes any
request whose `User-Agent` contains a URL or bare domain: it accepts the
connection and never responds, so the client hangs until timeout. Reproduced
4/4 with `indian-markets-mcp/0.1 (+https://github.com/...)` versus 4/4 success
with the bare token `indian-markets-mcp/0.1`. Variants using `+https://`,
parentheses or `contact=` all fail identically. The `User-Agent` in
`http.py` therefore carries no contact URL, and there is a comment saying why.

Cached 24 hours.

### NSE archives — bhavcopy
`nsearchives.nseindia.com` serves the daily bhavcopy ZIP to a plain client, no
block. Format is UDiFF (the post-2024 unified format), 34 columns, ~3,478 rows
per day. Cash equities are selected on `FinInstrmTp = STK` and then filtered to
equity series (`EQ`, `BE`, `BZ`, `SM`, `ST`).

Cached **permanently** (`ttl=-1`): a settled trading day's bhavcopy is
immutable, so re-fetching it can only waste the archive's bandwidth.

The archive doubles as the trading calendar — no published bhavcopy means no
trading that day, which is more reliable than a hardcoded holiday list.

### SEBI
`sebi.gov.in/robots.txt` disallows only `/js` and `/css`; order pages are
crawlable, and the orders are Indian government works.

This server does **not** fetch from SEBI. It reads a local SQLite corpus built
separately. That corpus currently holds **25 orders** — a sample, not SEBI's
enforcement archive. Both the tool description and every response carry that
warning, because a search tool covering a fraction of a percent of the archive
would otherwise let a model report "no SEBI order names this company" with
confidence it has not earned.

## Rate limiting

One second minimum between requests to the same host, enforced centrally in
`http.py` rather than left to callers, with exponential backoff on 5xx and 429.
404 is treated as a fact about the resource and not retried — a missing bhavcopy
means a market holiday, and retrying it three times is rude for no benefit.

## Not implemented, and why

**Corporate actions (dividends, splits, bonuses).** The spec asked for these. No
free source was found that serves them to an automated client under terms that
permit it: NSE's corporate-actions endpoint sits behind the same 403 as the
quote API, and the bhavcopy carries no corporate-action fields. Shipping a tool
that half-worked, or that silently scraped, was the worse option. There is no
tool for it.
