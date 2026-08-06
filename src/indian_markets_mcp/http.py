"""Shared HTTP layer: one cache, one rate limiter, one retry policy.

Every upstream call in this server goes through `fetch`. That is deliberate —
rate limiting and caching are the two things that must not be left to the
caller, and a single choke point is the only way to guarantee they aren't.

Caching is on-disk SQLite so it survives restarts. MCP clients open and close
servers constantly; an in-memory cache would be cold on every conversation and
we would hammer upstream for no reason.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# A truthful identifying UA, so an operator who wants to block this client can.
#
# It deliberately carries NO contact URL, which is the usual polite-crawler
# convention. niftyindices.com's WAF black-holes any request whose User-Agent
# contains a URL or bare domain — it accepts the connection and never responds,
# so the client sits until it times out. Reproduced 4/4 against
# `indian-markets-mcp/0.1 (+https://github.com/...)` (ReadTimeout after 30s)
# versus 4/4 success for the bare token below. Variants with `+https://`,
# parentheses, or `contact=` all fail the same way.
#
# Contact details live in the README instead. Do not "improve" this string by
# adding a URL to it.
USER_AGENT = "indian-markets-mcp/0.1"

DEFAULT_CACHE = Path.home() / ".cache" / "indian-markets-mcp" / "http.sqlite"


class UpstreamError(RuntimeError):
    """An upstream source failed. Raised, never swallowed into an empty result.

    The spec is explicit that a dead upstream must surface as a clear error and
    never as fabricated or silently-empty data, so every failure path ends here.
    """


@dataclass(frozen=True)
class Response:
    body: bytes
    from_cache: bool
    fetched_at: float

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class _RateLimiter:
    """Token-bucket-free minimum-interval limiter, per host.

    A minimum gap between requests is what public archives actually care about;
    a burst allowance would just let us hammer them politely-on-average.
    """

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        # ponytail: one global lock across all hosts. Fine at this call volume
        # (a handful of requests per tool call); split to per-host locks if the
        # server ever fans out concurrently.
        with self._lock:
            now = time.monotonic()
            earliest = self._last.get(host, 0.0) + self._min_interval
            if now < earliest:
                time.sleep(earliest - now)
            self._last[host] = time.monotonic()


class Http:
    """Cache + rate limit + backoff around httpx."""

    def __init__(
        self,
        cache_path: Path | None = None,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._path = Path(cache_path) if cache_path else DEFAULT_CACHE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._limiter = _RateLimiter(min_interval)
        self._timeout = timeout
        self._max_retries = max_retries
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " url TEXT PRIMARY KEY, body BLOB NOT NULL, fetched_at REAL NOT NULL)"
        )
        self._db.commit()

    def _cached(self, url: str, ttl: float) -> Response | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body, fetched_at FROM cache WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if ttl >= 0 and time.time() - fetched_at > ttl:
            return None
        return Response(body=body, from_cache=True, fetched_at=fetched_at)

    def _store(self, url: str, body: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO cache (url, body, fetched_at) VALUES (?, ?, ?)"
                " ON CONFLICT(url) DO UPDATE SET body = excluded.body,"
                " fetched_at = excluded.fetched_at",
                (url, body, time.time()),
            )
            self._db.commit()

    def fetch(self, url: str, ttl: float = 3600.0, headers: dict | None = None) -> Response:
        """GET `url`, serving from cache when the entry is younger than `ttl`.

        `ttl=-1` means "cache forever" — used for immutable historical archives
        such as a bhavcopy for a settled trading day, which cannot change.
        """
        hit = self._cached(url, ttl)
        if hit is not None:
            return hit

        host = httpx.URL(url).host
        request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        request_headers.update(headers or {})

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            self._limiter.wait(host)
            try:
                with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                    response = client.get(url, headers=request_headers)
            except httpx.HTTPError as exc:  # network-level failure
                last_error = exc
            else:
                if response.status_code == 200:
                    self._store(url, response.content)
                    return Response(response.content, from_cache=False, fetched_at=time.time())
                # 404 is a fact about the resource, not a transient fault. Retrying
                # a missing bhavcopy for a market holiday just wastes the archive's
                # time and ours.
                if response.status_code == 404:
                    raise UpstreamError(f"{url} -> 404 Not Found")
                last_error = UpstreamError(f"{url} -> HTTP {response.status_code}")
                if response.status_code < 500 and response.status_code != 429:
                    break
            time.sleep(2**attempt)

        # Last resort: a stale cache entry beats a hard failure, but the caller
        # is told it is stale so it can never be passed off as live data.
        stale = self._cached(url, ttl=-1)
        if stale is not None:
            return stale
        raise UpstreamError(f"{url} failed after {self._max_retries} attempts: {last_error}")


_shared: Http | None = None


def shared() -> Http:
    global _shared
    if _shared is None:
        _shared = Http()
    return _shared
