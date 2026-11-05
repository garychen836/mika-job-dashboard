"""The quote of the day: one quote per day, the same for every visitor (New Haven date), chosen in code, no model.

Files (written elsewhere, only read here): static/quotes.json  [{en, zh, who, where, url, orig?, tags[]}]
                                           static/occasions.json [{tag, en, zh, dates: {year: [date | range]}, window_days_before}]
A date is "YYYY-MM-DD"; a range is "YYYY-MM-DD/YYYY-MM-DD" (also ".." or [start, end]).
Order of candidates: 1) an occasion active today (inside its window) -> quotes with its tag; 2) tags of the dashboard's events within
7 days (career-fair, networking, interview, deadline); 3) every quote. Within the candidates the pick is a hash of the date, avoiding
any quote shown in the last 60 days. That history is recomputed from the date alone (no stored state) by replaying every day since a
fixed start date with occasions and the general pool; the event step applies only to the requested day. A missing or empty file gives no quote."""
from __future__ import annotations
import hashlib, json, os, re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATIC = Path(os.getenv("ELM_QUOTES_DIR") or Path(__file__).parent / "static")
EPOCH = date(2026, 1, 1)
AVOID_DAYS = 60
EVENT_DAYS = 7
EVENT_TAGS = (("career-fair", r"career fair|job fair|expo"), ("networking", r"network|mixer|coffee chat|alumni"),
              ("interview", r"interview|mock|case prep"), ("deadline", r"deadline|due\b|applications? (close|due)"))


def new_haven_today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _load(name: str) -> list[dict]:
    try:
        v = json.loads((STATIC / name).read_text())
        if isinstance(v, dict):
            v = v.get("quotes") or v.get("occasions") or []
        return [x for x in v if isinstance(x, dict)]
    except (OSError, ValueError):
        return []


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _spans(occ: dict, year: int) -> list[tuple[date, date]]:
    out = []
    # "dates" holds single days or "a/b" spans; "ranges" holds [start, end] pairs (months, Ramadan) - read both.
    for item in list((occ.get("dates") or {}).get(str(year), [])) + list((occ.get("ranges") or {}).get(str(year), [])):
        parts = item if isinstance(item, list) else re.split(r"\s*(?:/|\.\.|to)\s*", str(item).strip())
        a, b = _d(parts[0]), _d(parts[-1])
        if a and b:
            out.append((a, max(a, b)))
    return out


def active(day: date, occasions: list[dict]) -> list[dict]:
    """Occasions whose window covers day, the one starting soonest first."""
    hits = []
    for o in occasions:
        try:
            w = int(o.get("window_days_before") or 0)
        except (TypeError, ValueError):
            w = 0
        for yr in (day.year, day.year - 1):
            for a, b in _spans(o, yr):
                if a - timedelta(days=max(0, w)) <= day <= b:
                    hits.append((abs((a - day).days), o)); break
            else:
                continue
            break
    return [o for _, o in sorted(hits, key=lambda t: t[0])]


def event_tags(day: date, events: list[dict]) -> list[str]:
    """Tags of events starting within the next 7 days, soonest first."""
    rows = []
    for e in events or []:
        d = _d(e.get("date") or e.get("start"))
        if d and 0 <= (d - day).days <= EVENT_DAYS:
            text = f"{e.get('title', '')} {e.get('kind', '')} {' '.join(e.get('tags') or [])}".lower()
            rows += [((d - day).days, tag) for tag, rx in EVENT_TAGS if re.search(rx, text)]
    return list(dict.fromkeys(t for _, t in sorted(rows)))


def _h(day: date, salt: str = "") -> int:
    return int(hashlib.sha256(f"mika-job-quote|{day.isoformat()}|{salt}".encode()).hexdigest(), 16)


def _choose(day: date, cands: list[int], last: dict[int, date]) -> int:
    fresh = [i for i in cands if i not in last or (day - last[i]).days > AVOID_DAYS]
    if fresh:
        return fresh[_h(day) % len(fresh)]
    oldest = min(last.get(i, EPOCH) for i in cands)
    old = [i for i in cands if last.get(i, EPOCH) == oldest]
    return old[_h(day) % len(old)]


def _fresh(day: date, idx: list[int], last: dict[int, date]) -> bool:
    return any(i not in last or (day - last[i]).days > AVOID_DAYS for i in idx)


def _on_the_day(day: date, occ: dict) -> bool:
    """The occasion itself (not its lead-in window), when it lasts three days or less: its quotes win even if shown lately."""
    return any(x <= day <= y and (y - x).days <= 2 for yr in (day.year, day.year - 1) for x, y in _spans(occ, yr))


def _cands(day: date, quotes, occasions, last: dict[int, date] | None = None):
    """(candidate indexes, occasion|None) from the occasion step, else the general pool. An occasion whose quotes were
    all shown in the last AVOID_DAYS days (a month-long one, say) gives way to the general pool for that day."""
    for o in active(day, occasions):
        idx = [i for i, q in enumerate(quotes) if o.get("tag") in (q.get("tags") or [])]
        if idx and (last is None or _fresh(day, idx, last) or _on_the_day(day, o)):
            return idx, o
    return _general(quotes, occasions), None


def _general(quotes, occasions=()) -> list[int]:
    """The general pool: quotes with no tag, tagged "general", or with at least one tag that is not an occasion (a
    theme such as stoic or courage). A quote tagged only with occasions stays for its occasion. All quotes if none qualify."""
    occ = {o.get("tag") for o in occasions}
    g = [i for i, q in enumerate(quotes) if not q.get("tags") or "general" in q["tags"] or any(t not in occ for t in q["tags"])]
    return g or list(range(len(quotes)))


_replay: dict = {"key": None, "last": {}, "upto": EPOCH - timedelta(days=1)}


def _history(day: date, quotes, occasions) -> dict[int, date]:
    """When each quote was last shown before `day`, replayed from EPOCH (cached, extended day by day)."""
    key = (len(quotes), len(occasions), hashlib.sha1(json.dumps([quotes, occasions], sort_keys=True).encode()).hexdigest())
    if _replay["key"] != key or _replay["upto"] >= day:
        _replay.update(key=key, last={}, upto=EPOCH - timedelta(days=1))
    last, d = _replay["last"], _replay["upto"] + timedelta(days=1)
    while d < day:
        cands, occ = _cands(d, quotes, occasions, last)
        if occ is None:
            cands = _event_cands(d, quotes, None) or cands
        last[_choose(d, cands, last)] = d
        d += timedelta(days=1)
    _replay["upto"] = day - timedelta(days=1)
    return dict(last)


def _event_cands(day: date, quotes, events) -> list[int] | None:
    for tag in event_tags(day, events or []):
        idx = [i for i, q in enumerate(quotes) if tag in (q.get("tags") or [])]
        if idx:
            return idx
    return None


def pick(day: date, quotes: list[dict], occasions: list[dict], events: list[dict] | None = None) -> dict:
    """{"quote": {...}|None, "occasion": {en, zh}|None} for that day."""
    if not quotes:
        return {"quote": None, "occasion": None}
    last = _history(max(day, EPOCH), quotes, occasions)
    cands, occ = _cands(day, quotes, occasions, last)
    if occ is None:
        cands = _event_cands(day, quotes, events) or cands
    q = quotes[_choose(day, cands, last)]
    keep = ("en", "zh", "who", "where", "url", "orig")
    return {"quote": {k: q[k] for k in keep if k in q}, "occasion": {"en": occ.get("en"), "zh": occ.get("zh")} if occ else None}


def for_day(day: date | None = None, events: list[dict] | None = None) -> dict:
    return pick(day or new_haven_today(), _load("quotes.json"), _load("occasions.json"), events)
