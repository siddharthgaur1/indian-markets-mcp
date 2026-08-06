"""NSE end-of-day equity data from the public bhavcopy archive.

Why the archive and not the live quote API: `www.nseindia.com/api/quote-equity`
returns **HTTP 403 Access Denied** to any non-browser client (verified
2026-08-06). Getting past that means forging browser headers and cookie
handshakes to defeat an access control the exchange deliberately put there.
This server does not do that. See docs/SOURCES.md.

`nsearchives.nseindia.com` serves the daily bhavcopy ZIP to a plain client with
no such block, and NSE's robots.txt allows it. That gives official, settled
end-of-day OHLCV — which is what an LLM answering "how did X do" actually needs.

Format is UDiFF (the post-2024 unified format), columns verified 2026-08-06:

    TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,
    FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,
    ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,...
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from indian_markets_mcp.http import Http, UpstreamError, shared

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

# 'EQ' is ordinary rolling-settlement equity. 'BE' is trade-for-trade, 'GB' is
# sovereign gold bonds, and so on. Defaulting to EQ keeps a plain "quote for
# RELIANCE" from returning eight instrument types.
EQUITY_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}


@dataclass(frozen=True)
class Bar:
    date: str
    symbol: str
    series: str
    isin: str
    name: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    last: float | None
    prev_close: float | None
    volume: int | None
    turnover: float | None
    trades: int | None


def _num(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int(raw: str) -> int | None:
    value = _num(raw)
    return None if value is None else int(value)


def parse_bhavcopy(payload: bytes) -> list[Bar]:
    """Unzip and parse one day's bhavcopy into equity bars."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise UpstreamError("NSE bhavcopy download was not a valid ZIP") from exc

    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise UpstreamError("NSE bhavcopy ZIP contained no CSV")

    text = archive.read(names[0]).decode("utf-8", errors="replace")
    bars: list[Bar] = []
    for row in csv.DictReader(io.StringIO(text)):
        # Cash-segment stocks only: skip derivatives rows, which carry an expiry.
        if (row.get("FinInstrmTp") or "").strip() != "STK":
            continue
        bars.append(
            Bar(
                date=(row.get("TradDt") or "").strip(),
                symbol=(row.get("TckrSymb") or "").strip(),
                series=(row.get("SctySrs") or "").strip(),
                isin=(row.get("ISIN") or "").strip(),
                name=(row.get("FinInstrmNm") or "").strip(),
                open=_num(row.get("OpnPric", "")),
                high=_num(row.get("HghPric", "")),
                low=_num(row.get("LwPric", "")),
                close=_num(row.get("ClsPric", "")),
                last=_num(row.get("LastPric", "")),
                prev_close=_num(row.get("PrvsClsgPric", "")),
                volume=_int(row.get("TtlTradgVol", "")),
                turnover=_num(row.get("TtlTrfVal", "")),
                trades=_int(row.get("TtlNbOfTxsExctd", "")),
            )
        )
    return bars


def load_day(day: date, http: Http | None = None) -> list[Bar]:
    """Bhavcopy for one trading day.

    Cached forever (`ttl=-1`): a settled trading day's bhavcopy is immutable, so
    re-fetching it can only waste the archive's bandwidth.
    """
    http = http or shared()
    url = BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
    return parse_bhavcopy(http.fetch(url, ttl=-1).body)


def is_trading_day(day: date, http: Http | None = None) -> bool:
    """True when NSE published a bhavcopy for `day`.

    The archive itself is the calendar: no published bhavcopy means no trading.
    That is more reliable than a hardcoded holiday list, which rots every year.
    """
    try:
        return bool(load_day(day, http))
    except UpstreamError:
        return False


def latest_day(http: Http | None = None, max_lookback: int = 10) -> tuple[date, list[Bar]]:
    """Walk back from today to the most recent day with a published bhavcopy."""
    today = date.today()
    for offset in range(max_lookback):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:  # cheap skip; the archive is authoritative for holidays
            continue
        try:
            bars = load_day(day, http)
        except UpstreamError:
            continue
        if bars:
            return day, bars
    raise UpstreamError(
        f"No NSE bhavcopy found in the last {max_lookback} days. "
        "The archive may be down or the URL format may have changed."
    )


def eod_quote(symbol: str, day: date | None = None, http: Http | None = None) -> dict:
    """End-of-day OHLCV for one symbol."""
    wanted = symbol.strip().upper()
    if day is None:
        resolved, bars = latest_day(http)
    else:
        resolved, bars = day, load_day(day, http)

    matches = [b for b in bars if b.symbol == wanted and b.series in EQUITY_SERIES]
    if not matches:
        # A symbol that exists but did not trade, versus a symbol that does not
        # exist, are different answers. Say which.
        exists = any(b.symbol == wanted for b in bars)
        raise UpstreamError(
            f"{wanted} has no equity-series row in the NSE bhavcopy for {resolved.isoformat()}"
            + (" (it appears only in a non-equity series)" if exists else " (unknown symbol)")
        )
    return {"trade_date": resolved.isoformat(), "bars": [asdict(b) for b in matches]}


def eod_history(
    symbol: str, start: date, end: date, http: Http | None = None, max_days: int = 120
) -> dict:
    """OHLCV series for one symbol by walking daily bhavcopies.

    Each day is a separate archive file, so a long range means a lot of requests.
    `max_days` caps that: for multi-year history use the `nse-warehouse` project,
    which does the backfill once into Parquet instead of per-query.
    """
    wanted = symbol.strip().upper()
    if start > end:
        raise ValueError("start must be on or before end")
    span = (end - start).days + 1
    if span > max_days:
        raise ValueError(
            f"Range spans {span} days; this tool fetches one archive file per day "
            f"and is capped at {max_days}. Narrow the range."
        )

    series, missing = [], 0
    for offset in range(span):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        try:
            bars = load_day(day, http)
        except UpstreamError:
            missing += 1
            continue
        for bar in bars:
            if bar.symbol == wanted and bar.series in EQUITY_SERIES:
                series.append(asdict(bar))

    return {
        "symbol": wanted,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "count": len(series),
        "days_unavailable": missing,
        "series": series,
    }
