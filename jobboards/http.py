"""Polite HTTP: one request at a time per host, a gap between them,
Retry-After honoured, a host that keeps throttling left alone for the run,
and a cap on how much one response may be."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

import requests

USER_AGENT = "applyfinch-job-boards/0.1 (+https://github.com/applyEasy/job-boards)"
MAX_RETRY_AFTER = 60.0
THROTTLE_LIMIT = 3  # 429/503 answers from one host before the run stops asking it


class Throttled(RuntimeError):
    """The host has throttled us THROTTLE_LIMIT times this run."""


class TooLarge(RuntimeError):
    """The response is bigger than max_bytes."""


@dataclass
class Reply:
    status_code: int
    headers: Dict[str, str]
    content: bytes = field(repr=False)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


def _retry_after(headers: Dict[str, str]) -> float:
    try:
        return min(MAX_RETRY_AFTER, max(1.0, float(headers.get("Retry-After") or 5)))
    except ValueError:  # an HTTP date: don't parse, wait the default
        return 5.0


class Http:
    def __init__(self, min_gap: float = 0.5, timeout: float = 30, max_bytes: int = 30_000_000, *,
                 gaps: Optional[Dict[str, float]] = None, session: Any = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.min_gap, self.timeout, self.max_bytes, self.gaps = min_gap, timeout, max_bytes, gaps or {}
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.clock, self.sleep = clock, sleep
        self.throttled: Counter = Counter()
        self._last: Dict[str, float] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, url: str, **kw: Any) -> Reply:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Reply:
        return self.request("POST", url, **kw)

    def request(self, method: str, url: str, **kw: Any) -> Reply:
        host = (urlsplit(url).hostname or "").lower()
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:  # held while waiting, so one host gets one request at a time
            if self.throttled[host] >= THROTTLE_LIMIT:
                raise Throttled(host)
            reply = self._send(host, method, url, **kw)
            if reply.status_code in (429, 503):
                self.throttled[host] += 1
                self.sleep(_retry_after(reply.headers))
                reply = self._send(host, method, url, **kw)
            return reply

    def _send(self, host: str, method: str, url: str, **kw: Any) -> Reply:
        wait = self._last.get(host, float("-inf")) + self.gaps.get(host, self.min_gap) - self.clock()
        if wait > 0:
            self.sleep(wait)
        try:
            resp = self.session.request(method, url, timeout=self.timeout, stream=True, **kw)
            body = bytearray()
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > self.max_bytes:
                    raise TooLarge(f"{url} is over {self.max_bytes} bytes")
            resp.close()
            return Reply(resp.status_code, dict(resp.headers), bytes(body))
        finally:
            self._last[host] = self.clock()
