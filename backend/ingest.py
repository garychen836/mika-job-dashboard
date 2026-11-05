"""Public Yale career-event readers, taken from Mika's backend/ingest.py (only these: Yale School of the Environment career
site, Tsai CITY, Yale Schwarzman Center, Yale Engineering, and public calendar links). Every one is a public page whose
robots.txt allows it. RAW is this repo's data folder; elm/events.py points the *_FILE paths at a temp folder."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "elm" / "data"


TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


RANGE_RE = re.compile(TIME + r"\s*[–—-]\s*" + TIME, re.I)


SINGLE_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def _to24(h: int, m: int, mer: str) -> int:
    """Minutes after midnight."""
    h = h % 12 + (12 if mer.lower() == "pm" else 0)
    return h * 60 + m


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_times(text: str) -> tuple[str | None, str | None]:
    """'11:30am–12:30pm' -> ('11:30', '12:30'); '2:35pm' -> ('14:35', None); '12–1pm' -> ('12:00', '13:00')."""
    if not text:
        return None, None
    text = re.sub(r"\bnoon\b", "12:00pm", text, flags=re.I)
    for m in RANGE_RE.finditer(text):
        h1, m1, mer1, h2, m2, mer2 = m.groups()
        if not (mer1 or mer2) or int(h1) > 12 or int(h2) > 12:
            continue  # a date range or a Zoom id, not a time
        mer2 = mer2 or mer1
        end = _to24(int(h2), int(m2 or 0), mer2)
        start = _to24(int(h1), int(m1 or 0), mer1 or mer2)
        if not mer1 and start > end:  # "11–1pm" means 11am
            start = _to24(int(h1), int(m1 or 0), "am" if mer2.lower() == "pm" else "pm")
        return _fmt(start), _fmt(end)
    m = SINGLE_RE.search(text)
    if m and int(m.group(1)) <= 12:
        return _fmt(_to24(int(m.group(1)), int(m.group(2) or 0), m.group(3))), None
    return None, None


TRACKS, KINDS = {"academic", "recruiting", "campus"}, {"class", "assignment", "project", "exam", "office_hours", "review", "event"}


def clean_inbound(row: dict) -> dict:
    """Validate one pushed item and return it in the shape of the items table. Raises ValueError with the reason."""
    for key in ("id", "track", "kind", "date", "title"):
        if not str(row.get(key) or "").strip():
            raise ValueError(f"missing {key}")
    if row["track"] not in TRACKS or row["kind"] not in KINDS:
        raise ValueError(f"track must be one of {sorted(TRACKS)} and kind one of {sorted(KINDS)}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]):
        raise ValueError("date must be YYYY-MM-DD")
    for key in ("start", "end"):
        if row.get(key) and not re.fullmatch(r"\d{2}:\d{2}", row[key]):
            raise ValueError(f"{key} must be HH:MM (24-hour)")
    return {
        "id": "in-" + re.sub(r"[^a-zA-Z0-9_-]", "-", str(row["id"]))[:80],
        "track": row["track"], "kind": row["kind"], "date": row["date"],
        "start": row.get("start") or None, "end": row.get("end") or None,
        "title": str(row["title"])[:200], "detail": str(row.get("detail") or "")[:600],
        "location": row.get("location"), "source": str(row.get("source") or "inbound")[:40], "url": row.get("url"),
        "tags": [str(t)[:30] for t in (row.get("tags") or [])][:10], "course": row.get("course"), "host": row.get("host"),
    }


FEEDS, FEED_CACHE = RAW / "feeds.json", RAW / "feeds_cache"


def _feeds() -> list[dict]:
    return json.loads(FEEDS.read_text()) if FEEDS.exists() else []


def pull_feeds() -> dict:
    import calendar_io
    FEED_CACHE.mkdir(exist_ok=True)
    out = {}
    for f in _feeds():
        fid = re.sub(r"[^a-zA-Z0-9_-]", "-", str(f["id"]))[:40]
        try:
            (FEED_CACHE / f"{fid}.ics").write_text(calendar_io._fetch(f["url"]))
            out[fid] = "ok"
        except Exception as e:
            out[fid] = f"{type(e).__name__}: {e}"[:160]  # keep the last good copy
    return out


def _own_page(notes: str) -> str | None:
    """A Luma calendar feed gives no link field: the event's own page is in its text ("Get up-to-date information
    at: https://luma.com/..."). Without it the card can only point at the organiser's general events page."""
    m = (re.search(r"https://(?:luma\.com|lu\.ma)/(?:event/[A-Za-z0-9_-]+|(?!calendar\b|event\b)[A-Za-z0-9_-]+)", notes or "")
         or re.search(r"https://luma\.com/calendar/cal-[A-Za-z0-9]+", notes or ""))   # failing that, the organiser's Luma calendar, which lists it
    return m.group(0) if m else None


def _place(text: str) -> str | None:
    """A place as the feed gives it. CampusGroups writes "Sign in to download the location" where the place is for
    members only: that is not a place, so none is shown."""
    text = (text or "").lstrip("-– ").strip()
    return None if re.match(r"sign in to (download|see|view)", text, re.I) else text or None


def load_feeds() -> list[dict]:
    """Rows from every cached calendar link. An event two links carry (a club's own calendar and the school-wide one)
    is kept once, from the link listed first. A link's "skip" pattern drops titles not meant for MBA students, and its
    "only_host" pattern keeps only the office's own events (a club calendar also lists events other offices co-host)."""
    import calendar_io
    rows, seen = [], set()
    for f in _feeds():
        fid = re.sub(r"[^a-zA-Z0-9_-]", "-", str(f["id"]))[:40]
        path = FEED_CACHE / f"{fid}.ics"
        if not path.exists():
            continue
        for e in calendar_io.parse_ics(path.read_text()):
            if ((e["url"] and e["url"] in seen) or (f.get("skip") and re.search(f["skip"], e["title"], re.I))
                    or (f.get("only_host") and not re.search(f["only_host"], e["host"] or "", re.I))):   # another office's event co-listed there
                continue
            seen.add(e["url"])
            try:
                rows.append(clean_inbound({
                    "id": f"{fid}-{e['id']}", "track": f.get("track", "campus"), "kind": f.get("kind", "event"), "date": e["date"],
                    "start": e["start"], "end": e["end"], "title": e["title"], "location": _place(e["location"]),
                    "detail": re.sub(r"\s*-{3}\s*Event Details:.*$", "", e["notes"]), "source": f.get("source", fid),
                    "url": e["url"] or _own_page(e["notes"]) or f.get("page"), "tags": f.get("tags", []) + [t.split(":", 1)[-1].lower() for t in e["types"] if t.startswith("event_type:")],
                    "host": e["host"] or f.get("host"),
                }))
            except ValueError:
                continue
    return rows


def _plausible(rows: list, current: Path) -> bool:
    """Keep what was read only if it is not a fraction of what is on file: a half-loaded site or a changed page
    layout must not wipe out good data."""
    try:
        before = len(json.loads(current.read_text())) if current.exists() else 0
    except json.JSONDecodeError:
        before = 0
    return bool(rows) and len(rows) >= before * 0.5


LISTING_ITEM = re.compile(r'<li class="event_item[^"]*">\s*<a href="([^"]+)">(.*?)</a>\s*</li>', re.S)


MONTHS = {m: n for n, m in enumerate("January February March April May June July August September October November December".split(), 1)}


def pull_yse(max_pages: int = 10) -> str:
    return _pull_listing(YSE_URL, YSE_FILE, max_pages)


def _pull_listing(base: str, target: Path, max_pages: int) -> str:
    """An events listing on the career-site platform both offices use: title, date, time, place, link per event."""
    import html
    import time

    import httpx
    rows, seen = [], set()
    part = lambda cls, block: html.unescape(re.sub(r"<[^>]+>|\s+", " ", (re.search(rf'class="{cls}">(.*?)</(?:p|h3)>', block, re.S) or [None, ""])[1])).strip()
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "MikaJobDashboard/1.0 (student project; public pages only)"}) as web:
        for n in range(1, max_pages + 1):
            r = web.get(base if n == 1 else f"{base}page/{n}/")
            found = LISTING_ITEM.findall(r.text) if r.status_code == 200 else []
            if not found:
                break
            for url, block in found:
                days = re.findall(r"(\w+) (\d{1,2}), (\d{4})", part("event-date", block))
                if url in seen or not days or days[0][0] not in MONTHS:
                    continue
                seen.add(url)
                iso = [f"{y}-{MONTHS[m]:02d}-{int(d):02d}" for m, d, y in days if m in MONTHS]
                rows.append({"url": url, "title": part("title", block).replace("Event: ", "", 1).strip(), "date": iso[0], "until": iso[-1] if len(iso) > 1 else None,
                             "time": part("event-time", block), "location": part("event-location", block)})
            time.sleep(0.6)  # one visitor's pace
    if _plausible(rows, target):
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return f"{len(rows)} events"


YSE_URL, YSE_FILE = "https://careers.environment.yale.edu/events/", RAW / "yse_site.json"


TSAI_URL, TSAI_FILE = "https://city.yale.edu/events", RAW / "tsai_site.json"


def pull_tsai(limit: int = 30) -> str:
    """Tsai CITY lists its events as links to their sign-up pages. Each of those pages describes its own event in a
    machine-readable block (calendar_io.page_events), so the dates are exact and nothing is guessed."""
    import time

    import calendar_io
    import httpx
    page = httpx.get(TSAI_URL, timeout=30, follow_redirects=True, headers={"User-Agent": "MikaJobDashboard/1.0 (student project; public pages only)"})
    links = list(dict.fromkeys(re.findall(r'https://(?:lu\.ma|luma\.com)/[A-Za-z0-9_-]{4,}', page.text if page.status_code == 200 else "")))[:limit]
    rows = []
    for link in links:
        try:
            for e in calendar_io.fetch_page(link)[2][:1]:
                rows.append({"url": link, "title": e["title"], "date": e["date"], "start": e["start"], "end": e["end"], "location": e["location"]})
        except Exception:
            pass   # one page that will not open does not stop the rest
        time.sleep(0.6)
    if _plausible(rows, TSAI_FILE):
        TSAI_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return f"{len(rows)} events"


def load_yse() -> list[dict]:
    return [e | {"id": "yse-" + e["id"][4:], "track": "campus", "source": "yse", "host": "YSE Career Development"} for e in _load_listing(YSE_FILE)]


def load_tsai() -> list[dict]:
    if not TSAI_FILE.exists():
        return []
    return [{"id": "tsai-" + hashlib.sha1(e["url"].encode()).hexdigest()[:10], "track": "campus", "kind": "event", "date": e["date"],
             "start": e.get("start"), "end": e.get("end"), "title": e["title"], "detail": "", "location": e.get("location"),
             "source": "tsai-city", "url": e["url"], "tags": [], "course": None, "host": "Tsai CITY"} for e in json.loads(TSAI_FILE.read_text())]


SWZ_URL, SWZ_FILE = "https://schwarzman.yale.edu/events/upcoming", RAW / "schwarzman_site.json"


def pull_swz(limit: int = 40) -> str:
    """Yale Schwarzman Center lists its events as links to a page each, and every page says its own date and hours in a
    machine-readable block (calendar_io.page_events), so the times are exact."""
    import time

    import calendar_io
    import httpx
    page = httpx.get(SWZ_URL, timeout=30, follow_redirects=True, headers={"User-Agent": "MikaJobDashboard/1.0 (student project; public pages only)"})
    slugs = list(dict.fromkeys(re.findall(r'href="/events/((?!upcoming|calendar)[a-z0-9][a-z0-9-]{4,})"', page.text if page.status_code == 200 else "")))[:limit]
    rows = []
    for slug in slugs:
        url = "https://schwarzman.yale.edu/events/" + slug
        try:
            for e in calendar_io.fetch_page(url)[2][:1]:
                rows.append({"url": url, "title": e["title"], "date": e["date"], "start": e["start"], "end": e["end"], "location": ""})
        except Exception:
            pass   # one page that will not open does not stop the rest
        time.sleep(0.5)
    if _plausible(rows, SWZ_FILE):
        SWZ_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return f"{len(rows)} events"


def load_swz() -> list[dict]:
    if not SWZ_FILE.exists():
        return []
    return [{"id": "swz-" + hashlib.sha1(e["url"].encode()).hexdigest()[:10], "track": "campus", "kind": "event", "date": e["date"],
             "start": e.get("start"), "end": e.get("end"), "title": e["title"], "detail": "", "location": e.get("location") or "Yale Schwarzman Center",
             "source": "community", "url": e["url"], "tags": [], "course": None, "host": "Yale Schwarzman Center"} for e in json.loads(SWZ_FILE.read_text())]


SEAS_URL, SEAS_FILE = "https://engineering.yale.edu/news-and-events/events", RAW / "seas_site.json"


def pull_seas(max_pages: int = 6) -> str:
    """The School of Engineering & Applied Science lists its events on a public page that fills itself from a small
    data address named in that page. Read the address from the page (so a change of number does not break this),
    then ask it for each page of the list the way the page itself does."""
    import time
    from datetime import datetime

    import httpx
    head = {"User-Agent": "MikaJobDashboard/1.0 (student project; public pages only)"}
    page = httpx.get(SEAS_URL, timeout=30, follow_redirects=True, headers=head)
    found = re.search(r"https://engineering\.yale\.edu/news-and-events/events/get_events/\d+", page.text if page.status_code == 200 else "")
    rows = []
    clock = lambda t: datetime.strptime(t.strip().upper(), "%I:%M %p").strftime("%H:%M") if t and t.strip() else None
    for n in range(1, max_pages + 1) if found else ():
        r = httpx.post(found.group(0), timeout=30, headers=head,
                       data={"department": "", "onlyalldepartments": "false", "template": "listing", "max": "12", "currentPage": str(n)})
        if r.status_code != 200:
            break
        data = r.json()
        for e in data.get("events", []):
            try:
                rows.append({"url": e["link"], "title": " ".join(e["title"].split()), "date": datetime.strptime(e["date"].strip(), "%B %d, %Y").date().isoformat(),
                             "start": clock(e.get("startTime")), "end": clock(e.get("endTime")),
                             "departments": [d["name"] for d in e.get("departments", []) if d.get("name") and d["name"] != "All Departments"]})
            except (KeyError, ValueError):
                pass   # one entry in an unexpected shape does not stop the rest
        if not data.get("showMore"):
            break
        time.sleep(0.6)
    if _plausible(rows, SEAS_FILE):
        SEAS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return f"{len(rows)} events"


def load_seas() -> list[dict]:
    if not SEAS_FILE.exists():
        return []
    return [{"id": "seas-" + hashlib.sha1(e["url"].encode()).hexdigest()[:10], "track": "campus", "kind": "event", "date": e["date"],
             "start": e.get("start"), "end": e.get("end"), "title": e["title"], "detail": ", ".join(e.get("departments") or []), "location": None,
             "source": "yale-engineering", "url": e["url"], "tags": [], "course": None, "host": "Yale Engineering"} for e in json.loads(SEAS_FILE.read_text())]


def _load_listing(source_file: Path) -> list[dict]:
    if not source_file.exists():
        return []
    out = []
    for e in json.loads(source_file.read_text()):
        span = bool(e.get("until")) and e["until"] != e["date"]   # a sign-up window that runs for weeks sits on its last day
        # ...but an event of two or three days with hours (a conference, 8am - 5pm) starts on its first day, at its time
        short = span and (date.fromisoformat(e["until"]) - date.fromisoformat(e["date"])).days <= 2 and bool(parse_times((e.get("time") or "").replace(" ", ""))[0])
        if short:
            span = False
        start, end = (None, None) if span else parse_times((e.get("time") or "").replace(" ", ""))
        out.append({
            "id": "lst-" + re.sub(r"[^a-z0-9]+", "-", e["url"].rstrip("/").split("/events/")[-1].lower())[:60] + "-" + hashlib.sha1(e["url"].encode()).hexdigest()[:6], "track": "recruiting", "kind": "event",
            "date": e["until"] if span else e["date"], "span_from": e["date"] if span else None, "start": start, "end": end, "title": e["title"],
            "detail": f"Open {e['date']} to {e['until']}." if span else f"{e['date']} to {e['until']}, {e['time']} each day." if short else "", "location": e.get("location") or None, "source": "listing",
            "url": e["url"], "tags": [], "course": None, "host": "Career Development Office",
        })
    return out
