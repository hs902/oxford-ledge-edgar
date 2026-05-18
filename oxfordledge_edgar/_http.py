"""oxfordledge_edgar._http — minimal SEC EDGAR HTTP client.

Stdlib-only by design. No `requests`, no `httpx`. The whole HTTP
surface is `urllib.request` plus a thread-safe rate limiter.

Public API:

    RateLimiter(qps=8) — enforce a per-second rate cap across threads
    EdgarFetcher(user_agent=..., qps=8) — wraps urllib with rate limit
                                          and SEC-mandated User-Agent

SEC's published limit is 10 qps; the default of 8 qps leaves headroom
for natural request bursts and avoids 429s on busy days. Increase only
if you have documented SEC-side approval.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

_logger = logging.getLogger(__name__)


# SEC's Fair Access policy requires a User-Agent that identifies you
# AND provides a reachable contact (email or URL). Format that has
# worked in production: "<Company Name> <email>".
#
# See: https://www.sec.gov/os/accessing-edgar-data
_DEFAULT_USER_AGENT = "OxfordLedge research@oxfordledge.com"


class RateLimiter:
    """Thread-safe leaky-bucket rate limiter.

    Used by EdgarFetcher to enforce SEC's 10 qps cap. Default 8 qps
    leaves headroom for natural bursts and avoids occasional 429s.

    Usage:
        limiter = RateLimiter(qps=8)
        with limiter:
            response = urlopen(...)

    The context-manager form blocks until a token is available, then
    immediately releases. Concurrent threads share the same bucket.
    """

    def __init__(self, qps: float = 8.0) -> None:
        if qps <= 0:
            raise ValueError(f"qps must be positive; got {qps}")
        self.qps = float(qps)
        self._interval = 1.0 / self.qps
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def acquire(self) -> None:
        """Block until the rate-limit window allows the next request."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            # Schedule the next slot one interval ahead.
            self._next_allowed_at = now + self._interval

    def __enter__(self) -> "RateLimiter":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        # No-op: the slot was consumed at acquire(); nothing to release.
        return None


class EdgarFetcher:
    """Minimal SEC EDGAR HTTP fetcher with rate limit + User-Agent.

    Usage:
        fetcher = EdgarFetcher(user_agent="MyCompany research@mycompany.com")
        xml = fetcher.get_text("https://www.sec.gov/Archives/...")

    `user_agent` MUST identify you and provide a reachable contact, per
    SEC's Fair Access policy. Failing to provide a real contact will get
    your IP blocked. The default `_DEFAULT_USER_AGENT` references Oxford
    Ledge — replace it with your own when shipping in production.

    `qps` defaults to 8 (under SEC's 10 qps cap). Adjust only if you
    have a documented arrangement with SEC for higher rates.
    """

    def __init__(
        self,
        user_agent: str = _DEFAULT_USER_AGENT,
        qps: float = 8.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "EdgarFetcher requires a User-Agent with a contact email "
                "per SEC Fair Access policy. Format: "
                "'YourCompany contact@yourcompany.com'."
            )
        self.user_agent = user_agent
        self.timeout_seconds = float(timeout_seconds)
        self._limiter = RateLimiter(qps=qps)

    def get_text(self, url: str, max_bytes: Optional[int] = None) -> str:
        """Fetch URL and return body decoded as UTF-8.

        Raises urllib.error.HTTPError on non-2xx responses (callers
        decide retry policy). Honors `max_bytes` if set — useful for
        large EDGAR XML where you want to bail out early on
        unexpected payload size.
        """
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )
        with self._limiter:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    if max_bytes is not None and max_bytes > 0:
                        raw = resp.read(max_bytes)
                    else:
                        raw = resp.read()
                    # SEC EDGAR sometimes returns gzip-encoded payloads
                    # without explicit Content-Encoding when the client
                    # sends Accept-Encoding. urllib doesn't auto-decode;
                    # check the magic bytes manually.
                    if raw[:2] == b"\x1f\x8b":
                        import gzip
                        raw = gzip.decompress(raw)
                    return raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                _logger.warning(
                    "EdgarFetcher HTTPError %s on %s: %s", e.code, url, e
                )
                raise
            except Exception as e:
                _logger.warning("EdgarFetcher error on %s: %s", url, e)
                raise


__all__ = ["EdgarFetcher", "RateLimiter"]
