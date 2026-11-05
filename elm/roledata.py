"""Elm's data files (roles.json, events.json, the public pool): read once, reloaded when the file changes. Search over them.
Every role returned passes the original-link rule: its url is https on a real company or ATS host, never an aggregator."""
from __future__ import annotations
import hashlib, json, os, re, sys, threading
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import durable, postings

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


data_dir = durable.data_dir

# Hosts that are not a company's own site or its recruiting system: job boards and aggregators, link shorteners, search pages.
NOT_ORIGINAL = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "joinhandshake.com", "handshake.com", "monster.com",
                "simplyhired.com", "careerbuilder.com", "wellfound.com", "angel.co", "builtin.com", "dice.com", "jooble.org", "adzuna.com",
                "google.com", "bing.com", "bit.ly", "t.co", "tinyurl.com", "facebook.com", "twitter.com", "x.com", "reddit.com", "youtube.com")
_WD_HOST = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$")


def link_ok(role: dict) -> bool:
    """https, a named host (no address, no credentials), not an aggregator; a Workday role must be on a Workday host."""
    try:
        u = urlparse(role.get("url") or "")
        host = (u.hostname or "").lower()
        if u.scheme != "https" or "." not in host or u.username or u.password or re.fullmatch(r"[\d.]+|[\da-f:]+", host):
            return False
        if any(host == d or host.endswith("." + d) for d in NOT_ORIGINAL):
            return False
        return bool(_WD_HOST.match(host)) if role.get("ats") == "workday" else True
    except ValueError:
        return False


def event_id(e: dict) -> str:
    return e.get("id") if isinstance(e.get("id"), str) and e.get("id") else "ev-" + hashlib.sha1(
        f"{e.get('title')}|{e.get('date') or e.get('start')}|{e.get('url')}".encode()).hexdigest()[:10]


def all_roles() -> dict:
    """{roles: roles.json plus the public pool (source "found"), original links only, built}."""
    d = load("roles.json", "roles")
    pool = load("pool.json", "roles")["roles"]
    seen, out = set(), []
    for r in list(d["roles"]) + [{**p, "source": "found"} for p in pool]:
        if not link_ok(r):
            continue
        key = postings.canonical(r["url"])
        if key in seen:
            continue
        seen.add(key); out.append(r)
    return {"roles": out, "built": d["built"], "sources": d.get("sources")}


def roles_by_id(ids) -> list[dict]:
    m = {r.get("id"): r for r in all_roles()["roles"]}
    return [m[i] for i in ids if i in m]


def events_by_id(ids) -> list[dict]:
    m = {event_id(e): {**e, "id": event_id(e)} for e in load("events.json", "events")["events"]}
    return [m[i] for i in ids if i in m]


def load(name: str, key: str) -> dict:
    """{key: [records], built: str|None}. A missing or broken file gives an empty list and built None."""
    p = data_dir() / name
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {key: [], "built": None}
    with _lock:
        hit = _cache.get(str(p))
        if hit and hit[0] == mt:
            return hit[1]
        try:
            raw = json.loads(p.read_text())
        except (OSError, ValueError):
            return {key: [], "built": None}
        if isinstance(raw, list):
            raw = {key: raw}
        out = {key: [r for r in (raw.get(key) or []) if isinstance(r, dict)], "built": raw.get("built") or None,
               "sources": raw.get("sources")}
        _cache[str(p)] = (mt, out)
        return out


def _s(v) -> str:
    return v if isinstance(v, str) else ""


# The location filter: a fixed list of places, each matched in code against the posting's own location text. A role whose
# board lists only "3 Locations" (Workday) matches none of them: we do not guess where it is.
PLACES = {
    "nyc": r"\bnew york\b|\bnyc\b|\bbrooklyn\b|\bmanhattan\b|,\s*ny\b",
    "boston": r"\bboston\b|massachusetts|,\s*ma\b|\bcambridge,\s*ma|\bsomerville\b|\bwaltham\b",
    "ct": r"\bnew haven\b|connecticut|,\s*ct\b|\bstamford\b|\bhartford\b",
    "bay": r"san francisco|bay area|mountain view|palo alto|san mateo|santa clara|san jose|sunnyvale|menlo park|redwood city"
           r"|los altos|oakland|berkeley|cupertino|foster city|emeryville",
    "socal": r"los angeles|santa monica|irvine|pasadena|san diego|culver city|burbank|el segundo",
    "seattle": r"\bseattle\b|\bbellevue\b|\bredmond\b|,\s*wa\b",
    "dc": r"washington,?\s*d\.?\s?c\b|,\s*dc\b|\barlington,\s*va|\bmclean\b|\bbethesda\b",
    "chicago": r"\bchicago\b",
    "texas": r"\btexas\b|,\s*tx\b|\baustin\b|\bdallas\b|\bhouston\b",
    "remote": r"\bremote\b",
}
_PLACE_RE = {k: re.compile(v, re.I) for k, v in PLACES.items()}


# Co-op, in-semester and part-time roles are their own category (the owner, 2026-11-05): read from the title in code.
# Co-op and term words count only on internships (an analyst who reviews "Condo, Co-op" loans is not a co-op); part-time
# counts on any role.
_COOP = re.compile(r"\bco-?op\b|in[- ]semester|\bsemester\b"
                   r"|\b(fall|spring|winter|autumn)\b[^,;|]{0,30}\b(intern|internship)\b"
                   r"|\b(intern|internship)\b[^,;|]{0,30}\b(fall|spring|winter|autumn)\b", re.I)
_PART = re.compile(r"part[- ]time", re.I)


def is_coop(r: dict) -> bool:
    t = _s(r.get("title"))
    return bool(_PART.search(t)) or (r.get("kind") == "intern" and bool(_COOP.search(t)))


def in_place(r: dict, place: str) -> bool:
    rx = _PLACE_RE.get(place)
    return rx is None or bool(rx.search(_s(r.get("location"))))


def search_roles(q: str = "", kind: str = "", sponsor_only: bool = False, startup_only: bool = False,
                 us_only: bool = False, mba: bool = False, limit: int = 50, offset: int = 0, place: str = "") -> dict:
    d = all_roles()
    words = [w for w in q.lower().split() if w][:8]
    hits = []
    for r in d["roles"]:
        flags = r.get("flags") or []
        if "mba_only" in flags and not mba:
            continue
        coop = is_coop(r)
        if kind == "coop" and not coop:
            continue
        if kind in ("intern", "entry") and (r.get("kind") != kind or coop):
            continue
        sp = r.get("sponsor")
        if sponsor_only and not (isinstance(sp, dict) and (sp.get("lca_recent") or sp.get("lca_total"))):
            continue
        if startup_only and r.get("tier") != "startup":
            continue
        if us_only and not r.get("us"):
            continue
        if place and not in_place(r, place):
            continue
        if words:
            hay = f"{_s(r.get('company'))} {_s(r.get('title'))} {_s(r.get('location'))}".lower()
            if not all(w in hay for w in words):
                continue
        hits.append(dict(r, coop=True) if coop else r)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return {"total": len(hits), "roles": hits[offset:offset + limit], "built": d["built"]}


def search_events(q: str = "", limit: int = 30) -> dict:
    d = load("events.json", "events")
    words = [w for w in q.lower().split() if w][:8]
    hits = [e for e in d["events"]
            if all(w in f"{_s(e.get('title'))} {_s(e.get('where') or e.get('location'))} {_s(e.get('org'))}".lower() for w in words)]
    return {"total": len(hits), "events": [{**e, "id": event_id(e)} for e in hits[:max(1, min(int(limit), 100))]], "built": d["built"]}


# --- anonymous per-day signal counters (no address, no user id) ---
_sig_lock = threading.Lock()
SIGNAL_KINDS = ("up", "down", "apply")


def role_exists(role_id: str) -> bool:
    return any(r.get("id") == role_id for r in all_roles()["roles"])


def record_signal(role_id: str, kind: str, today: str) -> None:
    """Add one to today's counter for this role. The file holds role ids, kinds and counts only."""
    with _sig_lock:
        s = durable.read("_signals.json", {})
        for day in sorted(s)[:-60]:                       # keep at most 60 days
            del s[day]
        d = s.setdefault(today, {}).setdefault(role_id, {k: 0 for k in SIGNAL_KINDS})
        d[kind] = int(d.get(kind, 0)) + 1
        durable.write("_signals.json", s)
