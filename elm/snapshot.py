"""The day's data, published where a sleeping free server can always get it back.

A Render free instance loses its disk when it sleeps or restarts (2026-11-05: 2,762 uploaded roles vanished 30 minutes later).
So the daily crawl also publishes one file, elm-data.json.gz ({"roles", "events", "crawl"}), as an asset of the release
`data-latest` in this public repository, and the server loads it whenever roles.json is missing: at start-up, and again
(at most every 10 minutes) when a request finds the list empty.

The URL is fixed configuration (ELM_SNAPSHOT_URL), never from a request. Redirects are followed only to GitHub's own hosts.
The file goes through the same checks as an upload (upload.validate), and it never replaces data already on disk.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from urllib.parse import urlparse

import durable

DEFAULT_URL = "https://github.com/garychen836/mika-job-dashboard/releases/download/data-latest/elm-data.json.gz"
MAX_BYTES = 40 * 1024 * 1024
HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
_lock = threading.Lock()
_last = [0.0]
status = {"loaded": None, "error": None, "at": None}


def url() -> str:
    return (os.getenv("ELM_SNAPSHOT_URL") or DEFAULT_URL).strip()


def _get(u: str) -> bytes:
    import httpx
    with httpx.Client(timeout=60, follow_redirects=False, headers={"User-Agent": "MikaJobDashboard/1.0"}) as web:
        for _ in range(5):
            host = (urlparse(u).hostname or "").lower()
            if urlparse(u).scheme != "https" or host not in HOSTS:
                raise ValueError(f"snapshot host not allowed: {host}")
            r = web.get(u)
            if r.status_code in (301, 302, 303, 307, 308):
                u = r.headers.get("location", "")
                continue
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise ValueError("snapshot too large")
            return r.content
    raise ValueError("too many redirects")


def load(force: bool = False) -> int:
    """Fill in roles / events / crawl summary from the published snapshot when roles.json is missing. Returns roles stored."""
    if url().lower() in ("", "off") or (not force and (durable.data_dir() / "roles.json").exists()):
        return 0                                                 # "off": tests and the red team stay offline
    with _lock:
        if not force and (durable.data_dir() / "roles.json").exists():
            return 0
        _last[0] = time.time()
        try:
            raw = _get(url())
            doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            import upload
            clean, why = upload.validate(doc)
            if clean is None:
                raise ValueError(why)
            clean.pop("_dropped", None)
            n = 0
            for name, data in clean.items():
                if force or not (durable.data_dir() / name).exists():
                    upload._write(name, data)
                    n = len(data) if name == "roles.json" else n
            status.update(loaded=n, error=None, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            print(f"[snapshot] loaded {n} roles from {urlparse(url()).hostname}", flush=True)
            return n
        except Exception as e:                                   # noqa: BLE001 - a failed load leaves the site as it was
            status.update(error=f"{type(e).__name__}: {e}"[:200], at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            print(f"[snapshot] {status['error']}", flush=True)
            return 0


def ensure_soon() -> None:
    """Called when a request finds no roles: load in the background, at most once every 10 minutes."""
    if url().lower() in ("", "off") or (durable.data_dir() / "roles.json").exists() or time.time() - _last[0] < 600 or _lock.locked():
        return
    threading.Thread(target=load, daemon=True).start()
