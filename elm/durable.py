"""Elm's small JSON data files (pool, usage, signals, requests, stats counters), written to disk and, when ELM_DATABASE_URL is
set, also to Postgres. A Render free instance loses its disk on every restart, so restore() runs at start-up.

This reuses backend/store.py (Mika's write-through: keep / fetch / reconnect-once / errors to the ops log) under its own scope
"elm" instead of writing a second one. ELM_DATABASE_URL may also be sqlite:///file.db, which is how the restore path is tested.
With no URL only the files exist. A database failure never fails a request (store.py prints and moves on)."""
from __future__ import annotations
import importlib.util, json, os, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = "elm"
NAMES = ("pool.json", "_usage.json", "_signals.json", "_requests.json", "_stats.json", "roles.json", "events.json", "_crawl.json")   # the last three arrive by upload
_lock = threading.RLock()
_db = None


def data_dir() -> Path:
    return Path(os.getenv("ELM_DATA_DIR") or Path(__file__).parent / "data")


def _store():
    """backend/store.py loaded under another name (elm has no module called store, but the page-side `store` of Mika must not clash)."""
    global _db
    url = (os.getenv("ELM_DATABASE_URL") or "").strip()
    if not url:
        return None
    if _db is None:
        spec = importlib.util.spec_from_file_location("mika_store", ROOT / "backend" / "store.py")
        _db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_db)
    if _db.URL != url:
        _db._drop_connection()
        _db.URL = url
    return _db


def read(name: str, default):
    try:
        v = json.loads((data_dir() / name).read_text())
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def write(name: str, data) -> None:
    body = json.dumps(data, ensure_ascii=False)
    with _lock:
        p = data_dir() / name
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(body)
            os.replace(tmp, p)
        except OSError:
            pass
        db = _store()
        if db:
            db.keep(SCOPE, name, body)


def restore() -> int:
    """Write back every stored file that is missing on disk. Returns how many. No URL: nothing."""
    db = _store()
    if not db:
        return 0
    n = 0
    for name, body in db.fetch(SCOPE):
        if name in NAMES and not (data_dir() / name).exists():
            try:
                json.loads(body)
                (data_dir()).mkdir(parents=True, exist_ok=True)
                (data_dir() / name).write_text(body)
                n += 1
            except (OSError, ValueError):
                pass
    return n


def lock():
    return _lock
