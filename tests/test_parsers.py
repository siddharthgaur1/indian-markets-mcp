"""Parser tests. Offline by default — no upstream is contacted here.

The parsers are where this server can quietly lie: a format change upstream that
still parses, but parses wrong, is worse than a crash. These tests pin the exact
formats verified on 2026-08-06.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from indian_markets_mcp.http import UpstreamError
from indian_markets_mcp.sources import amfi, indices, nse_archives

NAV_SAMPLE = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

Aditya Birla Sun Life Mutual Fund

119551;INF209KA12Z1;INF209KA13Z9;ABSL Banking & PSU - DIRECT - IDCW;107.0167;05-Aug-2026
119552;INF209K01YM2;-;ABSL Banking & PSU - DIRECT - MONTHLY IDCW;117.3283;05-Aug-2026

Axis Mutual Fund

120503;INF846K01EW2;-;Axis Banking & PSU Debt Fund - Growth;N.A.;05-Aug-2026

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

HDFC Mutual Fund

120465;INF179K01XQ0;-;HDFC Flexi Cap Fund - Growth;1980.4410;05-Aug-2026
"""


def test_nav_parser_assigns_category_and_amc_correctly():
    schemes = amfi.parse_nav_all(NAV_SAMPLE)
    assert len(schemes) == 4
    by_code = {s.scheme_code: s for s in schemes}

    absl = by_code[119551]
    assert absl.amc == "Aditya Birla Sun Life Mutual Fund"
    assert absl.category.startswith("Open Ended Schemes(Debt")
    assert absl.nav == pytest.approx(107.0167)
    assert absl.nav_date == "2026-08-05"
    assert absl.isin_reinvestment == "INF209KA13Z9"

    # A new AMC under the same category must not inherit the previous AMC.
    assert by_code[120503].amc == "Axis Mutual Fund"
    # A new category must reset the AMC, not carry it across the boundary.
    assert by_code[120465].amc == "HDFC Mutual Fund"
    assert by_code[120465].category.startswith("Open Ended Schemes(Equity")


def test_nav_parser_keeps_missing_values_null():
    """'N.A.' must become None, never 0.0 — a zero NAV is a lie about the fund."""
    schemes = {s.scheme_code: s for s in amfi.parse_nav_all(NAV_SAMPLE)}
    assert schemes[120503].nav is None
    assert schemes[119552].isin_reinvestment is None  # '-' means absent


def test_nav_parser_ignores_the_header_row():
    assert all(s.scheme_code != 0 for s in amfi.parse_nav_all(NAV_SAMPLE))


BHAV_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)
BHAV_ROWS = [
    (
        "2026-08-05,2026-08-05,CM,NSE,STK,2885,INE002A01018,RELIANCE,EQ,,,,,RELIANCE INDUSTRIES,"
        "1400.00,1420.00,1395.00,1410.50,1411.00,1398.00,,1410.50,,,1000000,1410500000.00,50000,F1,1,,,,,"
    ),
    # A derivatives row: must be filtered out by FinInstrmTp.
    (
        "2026-08-05,2026-08-05,FO,NSE,STF,99,INE002A01018,RELIANCE,,2026-08-27,,,,RELIANCE FUT,"
        "1400.00,1420.00,1395.00,1410.50,1411.00,1398.00,,1410.50,,,10,100.00,5,F1,1,,,,,"
    ),
    # A gold bond: equity-typed but a non-equity series.
    (
        "2026-08-05,2026-08-05,CM,NSE,STK,19078,IN0020200104,SGBJUN28,GB,,,,,GOLDBONDS,"
        "14165.00,14385.00,14163.56,14361.72,14370.00,14150.00,,14361.72,,,279,3988132.68,52,F1,1,,,,,"
    ),
]


def _zip_bhavcopy(rows: list[str]) -> bytes:
    csv_text = "\n".join([BHAV_HEADER, *rows])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20260805_F_0000.csv", csv_text)
    return buf.getvalue()


def test_bhavcopy_parser_extracts_cash_equities_only():
    bars = nse_archives.parse_bhavcopy(_zip_bhavcopy(BHAV_ROWS))
    # The derivatives row (FinInstrmTp=STF) must be gone; the gold bond remains
    # a STK row and is filtered later, by series, not here.
    assert [b.symbol for b in bars] == ["RELIANCE", "SGBJUN28"]
    reliance = bars[0]
    assert reliance.close == pytest.approx(1410.50)
    assert reliance.volume == 1_000_000
    assert reliance.isin == "INE002A01018"
    assert reliance.date == "2026-08-05"


def test_bhavcopy_rejects_a_non_zip_payload():
    with pytest.raises(UpstreamError):
        nse_archives.parse_bhavcopy(b"<html>Access Denied</html>")


def test_eod_quote_filters_to_equity_series(monkeypatch):
    """A gold bond must not answer a request for an equity quote."""
    bars = nse_archives.parse_bhavcopy(_zip_bhavcopy(BHAV_ROWS))
    monkeypatch.setattr(nse_archives, "load_day", lambda day, http=None: bars)

    result = nse_archives.eod_quote("RELIANCE", date(2026, 8, 5))
    assert result["bars"][0]["symbol"] == "RELIANCE"

    # SGBJUN28 exists but only in series GB, so the error must say so rather
    # than claim the symbol is unknown.
    with pytest.raises(UpstreamError, match="non-equity series"):
        nse_archives.eod_quote("SGBJUN28", date(2026, 8, 5))

    with pytest.raises(UpstreamError, match="unknown symbol"):
        nse_archives.eod_quote("NOSUCHSYM", date(2026, 8, 5))


def test_eod_history_rejects_an_oversized_range():
    with pytest.raises(ValueError, match="capped"):
        nse_archives.eod_history("INFY", date(2020, 1, 1), date(2026, 1, 1))


def test_index_name_normalisation():
    assert indices._normalise("nifty50") == "NIFTY 50"
    assert indices._normalise("NIFTY-BANK") == "NIFTY BANK"
    assert indices._normalise("Nifty 500") == "NIFTY 500"
    with pytest.raises(KeyError):
        indices._normalise("NIFTY 9999")
