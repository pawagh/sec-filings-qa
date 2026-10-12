"""SEC EDGAR client: declared User-Agent, rate limiting, retries, and an on-disk cache.

Raw responses are cached under data/raw so re-runs (and tests) don't re-hit SEC.
JSON API responses expire after `api_cache_ttl_hours`; filing documents are immutable
and cached forever.
"""

import json
import logging
import threading
import time
from pathlib import Path

import requests

from secqa.config import get_settings

log = logging.getLogger(__name__)

DATA_HOST = "https://data.sec.gov"
WWW_HOST = "https://www.sec.gov"


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: Path | None = None,
                 max_rps: float | None = None, ttl_hours: float | None = None, refresh: bool = False):
        s = get_settings()
        self.user_agent = user_agent or s.sec_user_agent
        if "@" not in self.user_agent:
            raise ValueError("SEC_USER_AGENT must include a contact email, e.g. 'MyApp me@example.com'")
        self.cache_dir = Path(cache_dir or s.cache_dir)
        self.min_interval = 1.0 / (max_rps or s.sec_max_rps)
        self.ttl_seconds = (ttl_hours if ttl_hours is not None else s.api_cache_ttl_hours) * 3600
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.http_requests = 0

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _get(self, url: str) -> bytes:
        for attempt in range(5):
            self._throttle()
            resp = self.session.get(url, timeout=60)
            self.http_requests += 1
            if resp.status_code == 200:
                return resp.content
            if resp.status_code in (429, 500, 502, 503, 504):
                backoff = 2 ** attempt
                log.warning("SEC %s on %s, retrying in %ss", resp.status_code, url, backoff)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"giving up on {url}")

    def _cached(self, rel: str, url: str, ttl: float | None) -> bytes:
        path = self.cache_dir / rel
        if path.exists() and not self.refresh and (ttl is None or time.time() - path.stat().st_mtime < ttl):
            return path.read_bytes()
        body = self._get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return body

    def company_tickers(self) -> dict:
        return json.loads(self._cached("company_tickers.json", f"{WWW_HOST}/files/company_tickers.json",
                                       self.ttl_seconds))

    def company_facts(self, cik: int) -> dict:
        return json.loads(self._cached(f"companyfacts/CIK{cik:010d}.json",
                                       f"{DATA_HOST}/api/xbrl/companyfacts/CIK{cik:010d}.json",
                                       self.ttl_seconds))

    def submissions(self, cik: int) -> dict:
        return json.loads(self._cached(f"submissions/CIK{cik:010d}.json",
                                       f"{DATA_HOST}/submissions/CIK{cik:010d}.json", self.ttl_seconds))

    def submissions_page(self, name: str) -> dict:
        # Older filings for heavy filers live in paged files, e.g. CIK0000019617-submissions-001.json
        return json.loads(self._cached(f"submissions/{name}", f"{DATA_HOST}/submissions/{name}",
                                       self.ttl_seconds))

    @staticmethod
    def filing_url(cik: int, accession: str, primary_doc: str) -> str:
        return f"{WWW_HOST}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_doc}"

    def filing_index(self, cik: int, accession: str) -> list[dict]:
        """Files in a filing folder: [{name, size, ...}]."""
        acc = accession.replace("-", "")
        body = self._cached(f"filings/{cik}/{accession}/index.json",
                            f"{WWW_HOST}/Archives/edgar/data/{cik}/{acc}/index.json", ttl=None)
        return json.loads(body)["directory"]["item"]

    def filing_document(self, cik: int, accession: str, primary_doc: str) -> bytes:
        return self._cached(f"filings/{cik}/{accession}/{primary_doc}",
                            self.filing_url(cik, accession, primary_doc), ttl=None)
