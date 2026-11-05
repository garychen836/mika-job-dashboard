"""Public statistics (counts only) for /stats. Counters per day in _stats.json; the rest is summed from files that already hold
counts (_usage.json, _signals.json, _requests.json, pool.json, _crawl.json). Never an address, a hash, a raw request or a person.

Unique visitors: a salted hash of address + browser, salt random for the day, kept in memory only and dropped at midnight; only the
number reaches the file. (Named elmstats because Mika's backend already has a module called stats.)"""
from __future__ import annotations
import hashlib, secrets, threading
from datetime import date

import durable, usage

FILE = "_stats.json"
STAGES = ("saved", "applied", "interview", "offer", "closed")
GROUPS = ("comp", "eng", "sci", "soc", "hum", "other")
_mem = {"day": "", "salt": b"", "seen": set()}
_lock = threading.Lock()


def _bump(path: list[str], n: int = 1) -> None:
    today = date.today().isoformat()
    with durable.lock():
        s = durable.read(FILE, {})
        for day in sorted(s)[:-120]:
            del s[day]
        d = s.setdefault(today, {})
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = int(d.get(path[-1], 0)) + n
        durable.write(FILE, s)


def count(name: str) -> None:
    _bump([name])


def today(name: str) -> int:
    return int(durable.read(FILE, {}).get(date.today().isoformat(), {}).get(name, 0))


def visit(ip: str, ua: str) -> None:
    day = date.today().isoformat()
    with _lock:
        if _mem["day"] != day:
            _mem.update(day=day, salt=secrets.token_bytes(16), seen=set())
        h = hashlib.sha256(_mem["salt"] + f"{ip}|{ua}".encode()).hexdigest()
        new = h not in _mem["seen"]
        _mem["seen"].add(h)
    _bump(["visits"])
    if new:
        _bump(["uniques"])


def interest(chips: list[str], kind: str, groups: list[str]) -> None:
    for c in chips:
        _bump(["chips", c])
    if kind:
        _bump(["kinds", kind])
    for g in groups:
        _bump(["groups", g])


def track(stage: str) -> None:
    _bump(["track", stage])


def _sum(days: dict, key: str) -> dict:
    out: dict = {}
    for d in days.values():
        for k, v in (d.get(key) or {}).items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def summary() -> dict:
    import json, search
    s = durable.read(FILE, {})
    days = sorted(s)[-14:]
    sig = {"up": 0, "down": 0, "apply": 0}
    for day in durable.read("_signals.json", {}).values():
        for rec in day.values():
            for k in sig:
                sig[k] += int(rec.get(k, 0))
    try:
        crawl = json.loads((durable.data_dir() / "_crawl.json").read_text())
        crawl = {k: crawl.get(k) for k in ("finished", "boards", "boards_ok", "roles", "seconds")}
    except (OSError, ValueError):
        crawl = None
    import pool
    return {"per_day": [{"day": d, "visits": s[d].get("visits", 0), "uniques": s[d].get("uniques", 0), "searches": s[d].get("searches", 0)} for d in days],
            "chips": _sum(s, "chips"), "kinds": _sum(s, "kinds"), "groups": _sum(s, "groups"), "tracking": _sum(s, "track"),
            "signals": sig, "searches_total": sum(d.get("searches", 0) for d in s.values()), "pool_size": pool.size(),
            "requests": search.request_count(), "request_words": search.words_top(10), "usage": usage.by_day(14),
            "dollars_today": round(usage.cost_today(), 5), "dollar_budget": usage.budget(), "crawl": crawl}
