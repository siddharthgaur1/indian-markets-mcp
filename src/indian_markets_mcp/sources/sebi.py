"""SEBI enforcement order search over a local corpus.

**Corpus size is 25 orders, not a full archive.** The `sebi-explorer` database
this reads holds 25 rows (verified 2026-08-06). The tool description exposed to
the LLM says so explicitly and every response carries `corpus_size`, because a
search tool that silently covers 0.2% of SEBI's enforcement history would let a
model state "no SEBI order names this company" with unearned confidence.

Terms: SEBI's robots.txt disallows only /js and /css — order pages are
crawlable. The orders themselves are Indian government works. This module does
not fetch from SEBI at all; it reads a database built separately.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Default location: the sebi-explorer sibling checkout. Override with
# INDIAN_MARKETS_MCP_SEBI_DB when the corpus lives elsewhere.
DEFAULT_DB = Path.home() / "Projects" / "sebi-explorer" / "data" / "sebi_orders.db"


class CorpusUnavailable(RuntimeError):
    """The SEBI corpus is not present. Distinct from 'present but no matches'."""


def db_path() -> Path:
    return Path(os.environ.get("INDIAN_MARKETS_MCP_SEBI_DB", DEFAULT_DB))


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise CorpusUnavailable(
            f"SEBI corpus not found at {path}. Build it with the sebi-explorer "
            "scraper, or set INDIAN_MARKETS_MCP_SEBI_DB to its location."
        )
    # Read-only URI plus query_only: this server has no business writing to the
    # corpus, and the cheapest way to guarantee that is to make it impossible.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def corpus_size() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def search_orders(
    query: str = "",
    entity: str | None = None,
    year: int | None = None,
    limit: int = 20,
) -> dict:
    """Search the local SEBI enforcement order corpus.

    Parameterised throughout — the LLM-supplied `query` never reaches SQL as
    text, only as a bound parameter.
    """
    clauses: list[str] = []
    params: list[object] = []

    if query.strip():
        clauses.append("(title LIKE ? OR entity LIKE ? OR violation_type LIKE ?)")
        needle = f"%{query.strip()}%"
        params += [needle, needle, needle]
    if entity:
        clauses.append("entity LIKE ?")
        params.append(f"%{entity.strip()}%")
    if year is not None:
        clauses.append("year = ?")
        params.append(year)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT order_date, year, month, title, entity, violation_type, url "
        f"FROM orders {where} ORDER BY order_date DESC LIMIT ?"
    )
    params.append(max(1, min(limit, 100)))

    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    return {
        "count": len(rows),
        "corpus_size": total,
        "coverage_warning": (
            f"This corpus holds {total} SEBI orders — a small sample, not SEBI's full "
            "enforcement archive. Absence of a match here does NOT mean SEBI has "
            "issued no order against the entity."
        ),
        "orders": rows,
    }
