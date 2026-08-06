"""Live tests. Deselected by default; run with `pytest -m live`.

These hit real upstream sources, so they fail when NSE or AMFI is down. That is
useful information, but it must never be what a CI red build means, so they are
separated rather than mocked into meaninglessness.
"""

from __future__ import annotations

import pytest

from indian_markets_mcp.sources import amfi, indices, nse_archives

pytestmark = pytest.mark.live


def test_amfi_nav_file_still_parses():
    schemes = amfi.load_schemes()
    assert len(schemes) > 10_000, "AMFI scheme count collapsed; check the format"
    assert any(s.nav is not None for s in schemes)
    assert all(s.amc for s in schemes), "every scheme must carry an AMC"


def test_index_constituent_counts_match_the_index_definitions():
    for name, expected in [("NIFTY 50", 50), ("NIFTY 100", 100), ("NIFTY 500", 500)]:
        assert indices.constituents(name)["count"] == expected


def test_bhavcopy_latest_day_has_plausible_shape():
    day, bars = nse_archives.latest_day()
    assert len(bars) > 1_000, f"only {len(bars)} rows for {day}"
    assert any(b.symbol == "RELIANCE" for b in bars)
