"""Per-address counters kept in memory only (no address ever reaches a file). Same idea as Mika's main._slow_down."""
from __future__ import annotations
import threading, time

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def peek(bucket: str, ip: str, limit: int, window: float = 86400) -> bool:
    """True if one more hit would still be within the limit."""
    now = time.time()
    with _lock:
        return len([t for t in _hits.get(bucket + "|" + ip, []) if now - t < window]) < limit


def hit(bucket: str, ip: str, limit: int, window: float = 86400) -> bool:
    """Count one hit and return True, or return False (nothing counted) when the limit is reached."""
    now = time.time()
    key = bucket + "|" + ip
    with _lock:
        if len(_hits) > 8000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 86400]:
                del _hits[k]
        ts = [t for t in _hits.get(key, []) if now - t < window]
        if len(ts) >= limit:
            _hits[key] = ts
            return False
        _hits[key] = ts + [now]
    return True


def reset() -> None:
    with _lock:
        _hits.clear()
