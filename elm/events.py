"""Career-related events at Yale from public sources Mika already reads -> elm/data/events.json. Code only, no model.

    python elm/events.py

Reused from backend/ingest.py (its own readers, pointed at a scratch folder): Yale School of the
Environment events, Tsai CITY, Yale Schwarzman Center, Yale Engineering, and the public calendar links of Jackson, OISS,
Yale Ventures, Tsai CITY and the Graduate Writing Lab (Poorvu). Never used: any school-internal career office or student-group page,
events.yale.edu (robots.txt disallows it)
or ocs.yale.edu (login). A hard rule in code drops any event whose link is on an SOM host. Beyond Yale: no verified public
feed, so extra_events() is an empty hook.

events.json: {"built": date, "events": [record]}. Record: {id, title, start, end, location, url (the source page), source, host, tags}. start/end are "YYYY-MM-DD" or
"YYYY-MM-DDTHH:MM" (New Haven time). Only events from today on are kept.
"""
import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
OUT = HERE / "data" / "events.json"

PUBLIC_FEEDS = ("jackson", "oiss", "yale-ventures", "tsai-city", "gwl")      # ids in elm/data/feeds.json
BANNED_HOSTS = ("som.yale.edu", "events.yale.edu", "ocs.yale.edu", "calendar.yale.edu")   # SOM-internal, robots-disallowed, login

# Broad words are read in the TITLE only (a lecture's description often says "founder" or "interview" in passing); the
# description is read only for the strong words in STRONG.
CAREER = re.compile(
    r"career|recruit|info(?:rmation)? session|internship|\bjobs?\b|networking|fellowship|alumn[ia]|employer|\bfair\b|expo\b|"
    r"r[eé]sum[eé]|cover letter|linkedin|interview|hiring|job search|work authori[sz]ation|\bopt\b|\bcpt\b|h-?1b|"
    r"workshop.{0,40}(?:skill|communicat|negotiat)|(?:skill|communicat|negotiat).{0,40}workshop|"
    r"startup|founder|entrepreneur|pitch|venture", re.I)
STRONG = re.compile(r"career|recruit|internship|job (?:fair|search)|hiring|r[eé]sum[eé]|work authori[sz]ation", re.I)
TAGS = (
    ("career-fair", r"career fair|\bfair\b|expo\b"),
    ("networking", r"networking|mixer|reception|alumn[ia]|coffee chat|meet (?:the|with)"),
    ("interview", r"interview|mock"),
    ("deadline", r"deadline|applications? (?:due|open|close)|apply by"),
    ("startups", r"startup|founder|entrepreneur|venture|pitch|tsai|innovator"),
    ("resume", r"r[eé]sum[eé]|cover letter|linkedin"),
    ("info-session", r"info(?:rmation)? session|employer|recruit|hiring"),
    ("visa", r"\bopt\b|\bcpt\b|h-?1b|visa|work authori[sz]ation"),
    ("jobs", r"\bjobs?\b|internship|career"),
    ("fellowship", r"fellowship"),
    ("panel", r"\bpanel\b|fireside|speaker"),
    ("workshop", r"workshop|skills"),
)


def is_career(title: str, detail: str = "") -> bool:
    return bool(CAREER.search(title or "") or STRONG.search(detail or ""))


def tags_for(title: str, detail: str = "") -> list[str]:
    return [t for t, rx in TAGS if re.search(rx, title or "", re.I)] or ["career"]


def _banned(url: str) -> bool:
    h = (urlparse(url or "").hostname or "").lower()
    return not h or any(h == b or h.endswith("." + b) for b in BANNED_HOSTS)


def _stamp(day: str, clock: str | None) -> str:
    return f"{day}T{clock}" if clock and re.fullmatch(r"\d{2}:\d{2}", clock) else day


def shape(e: dict, today: str) -> dict | None:
    """One Mika timeline row -> an Elm event, or None (past, not career-related, no https link, or a banned host)."""
    url, day = (e.get("url") or "").strip(), e.get("date") or ""
    if not url.startswith("https://") or _banned(url) or day < today or not e.get("title"):
        return None
    title = " ".join(str(e["title"]).split())[:200]
    detail = (e.get("detail") or "")[:600]
    if not is_career(title, detail):
        return None
    return {"id": "ev-" + hashlib.sha1(url.encode()).hexdigest()[:12], "title": title, "start": _stamp(day, e.get("start")),
            "end": _stamp(day, e.get("end")) if e.get("end") else None, "location": ((e.get("location") or "").strip()[:120] or None),
            "url": url, "source": e.get("source") or "yale", "host": e.get("host"), "tags": tags_for(title, detail)}


def extra_events() -> list[dict]:
    """HOOK: employer / outside-Yale events from a verified public feed, as Mika-style rows ({url, date, start, end, title, ...}).
    None verified yet (research 2026-11-05), so nothing."""
    return []


def read_sources(scratch: Path) -> tuple[list[dict], dict]:
    """Mika's readers, writing only into `scratch`. Returns (rows, per-source status)."""
    import ingest
    ingest.YSE_FILE, ingest.TSAI_FILE = scratch / "yse.json", scratch / "tsai.json"
    ingest.SWZ_FILE, ingest.SEAS_FILE = scratch / "swz.json", scratch / "seas.json"
    feeds = [f for f in json.loads((ingest.RAW / "feeds.json").read_text()) if f["id"] in PUBLIC_FEEDS]
    (scratch / "feeds.json").write_text(json.dumps(feeds))
    ingest.FEEDS, ingest.FEED_CACHE = scratch / "feeds.json", scratch / "feeds_cache"
    status, rows = {}, []
    for name, pull, load in (("yse", ingest.pull_yse, ingest.load_yse), ("tsai-city", ingest.pull_tsai, ingest.load_tsai),
                             ("schwarzman", ingest.pull_swz, ingest.load_swz), ("yale-engineering", ingest.pull_seas, ingest.load_seas)):
        try:
            status[name] = pull()
            rows += load()
        except Exception as e:                       # one source down must not stop the others
            status[name] = f"{type(e).__name__}: {str(e)[:80]}"
    try:
        status["calendar links"] = ingest.pull_feeds()
        rows += ingest.load_feeds()
    except Exception as e:
        status["calendar links"] = f"{type(e).__name__}: {str(e)[:80]}"
    return rows + extra_events(), status


def build(rows: list[dict] | None = None, today: str | None = None) -> list[dict]:
    today, out, seen = today or date.today().isoformat(), [], set()
    for e in rows or []:
        ev = shape(e, today)
        if ev and ev["url"] not in seen:
            seen.add(ev["url"])
            out.append(ev)
    return sorted(out, key=lambda x: (x["start"], x["title"]))


if __name__ == "__main__":
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        rows, status = read_sources(Path(tmp))
    ev = build(rows)
    if ev:                                           # nothing read keeps the old file
        OUT.parent.mkdir(exist_ok=True)
        tmp_file = OUT.with_suffix(".tmp")
        tmp_file.write_text(json.dumps({"built": date.today().isoformat(), "events": ev}, ensure_ascii=False, indent=1))
        tmp_file.replace(OUT)
    from collections import Counter
    print(f"{len(ev)} career events of {len(rows)} read in {time.time() - t0:.0f}s; by source {dict(Counter(e['source'] for e in ev))}; status {status}")
