"""Polite HTTP helper: per-host rate limiting, retries, identifying User-Agent."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

USER_AGENT = "ResearchDiscoveryTool/1.0 (+mailto:{contact})"

# Minimum seconds between requests to the same host. arXiv asks for >= 3s.
HOST_DELAYS = {
    "export.arxiv.org": 3.0,
    "arxiv.org": 3.0,
    "api.biorxiv.org": 1.0,
    "www.biorxiv.org": 2.0,
    "www.medrxiv.org": 2.0,
}
DEFAULT_DELAY = 1.0


class HttpClient:
    def __init__(self, contact_email: str = "unknown@example.com", timeout: int = 30, retries: int = 3):
        self.user_agent = USER_AGENT.format(contact=contact_email)
        self.timeout = timeout
        self.retries = retries
        self._last_hit: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        delay = HOST_DELAYS.get(host, DEFAULT_DELAY)
        wait = self._last_hit.get(host, 0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        host = urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(host)
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read(max_bytes)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt * 3)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                time.sleep(2 ** attempt * 2)
        raise RuntimeError(f"GET failed after {self.retries} attempts: {url}: {last_err}")
