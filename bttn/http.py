import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class Http:
    """TLS-verified, bounded HTTP with a replayable public source archive."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BTTN/2.0)"})
        retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"], respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get(self, url: str, **kwargs):
        if urlparse(url).scheme != "https":
            raise ValueError("Only HTTPS sources are accepted")
        response = self.session.get(url, timeout=(10, 25), **kwargs)
        response.raise_for_status()
        if len(response.content) > 15_000_000:
            raise ValueError("Source exceeds 15MB")
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        (self.directory / f"{key}.bin").write_bytes(response.content)
        (self.directory / f"{key}.json").write_text(json.dumps({
            "url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "content_type": response.headers.get("Content-Type", ""),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return response
