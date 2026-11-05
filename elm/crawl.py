"""Elm crawl: read every seed board once and write elm/data/roles.json (internships and entry-level roles, US or unknown
location). Code only: no model call. Public ATS endpoints through postings._get_json and workday.search/detail (fixed hosts,
no redirects). One board failing never stops the others; a pull where every board fails keeps the previous file.
Boards are read by a small thread pool (POOL threads) with a minimum gap per host (gate), and the pull stops starting new
boards after MAX_SECONDS. Workday boards from elm/data/seeds.json are read through elm-side callers that check only the
Workday URL shape plus "the board is in Elm's own seeds" (workday.allowed stays Mika's hand-checked list).

    python elm/crawl.py          -> one-line summary; also writes elm/data/_crawl.json (counts per company, failures)
"""
import hashlib
import html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE.parent))
import postings, sponsor, workday  # noqa: E402
from elm import entry  # noqa: E402

DATA = HERE / "data"
SEEDS, ROLES, SUMMARY = DATA / "seeds.json", DATA / "roles.json", DATA / "_crawl.json"
WD_QUERIES = ("intern", "internship", "analyst", "associate", "graduate", "entry level", "new grad", "co-op")
TIER = {"startup": "startup", "big": "big"}
POOL, MAX_SECONDS = 5, 25 * 60
SPONSORS = DATA / "sponsors.json"
GAP = {"greenhouse": 0.25, "lever": 0.8, "ashby": 0.35}      # minimum seconds between two requests to the same host
_gate_lock, _gate_last = threading.Lock(), {}


def gate(key: str, gap: float = 0.3) -> None:
    """Per-host politeness across threads: wait until `gap` seconds passed since the last request to `key`."""
    with _gate_lock:
        now = time.monotonic()
        at = max(now, _gate_last.get(key, 0) + gap)
        _gate_last[key] = at
    if at > now:
        time.sleep(at - now)


_SP: dict | None = None


def sponsor_hit(company: str) -> dict | None:
    """Sponsor facts for a company: Elm's own DOL table (elm/data/sponsors.json, >= 20 certified in the last 2 FY) first,
    then Mika's backend/sponsor.lookup. Both match on sponsor.norm. Returns {recent_count, certified, recent_fy} or None."""
    global _SP
    if _SP is None:
        try:
            _SP = json.loads(SPONSORS.read_text()).get("employers") or {}
        except (OSError, ValueError):
            _SP = {}
    key = sponsor.norm(company)
    h = _SP.get(key) if key else None
    if h:
        return {"recent_count": h.get("certified_recent_2fy"), "certified": h.get("certified_total"), "recent_fy": h.get("last_fy")}
    return sponsor.lookup(company)


# ---- Workday, elm-side: the board must be in Elm's own seeds (explicit allow-list), URL parts must have Workday's shape ----
_WD_ALLOW: set = set()


def _wd_ok(b: dict) -> bool:
    return (bool(workday.HOST_RE.match(str(b.get("host") or ""))) and bool(workday.PART_RE.match(str(b.get("tenant") or "")))
            and bool(workday.PART_RE.match(str(b.get("site") or ""))) and (b["host"], b["tenant"], b["site"]) in _WD_ALLOW)


def allow_workday(boards: list[dict]) -> None:
    _WD_ALLOW.clear()
    _WD_ALLOW.update((b["host"], b["tenant"], b["site"]) for b in boards if b.get("ats") == "workday" and b.get("host"))


def wd_search(b: dict, text: str, limit: int = 20) -> list[dict]:
    """workday.search for a board in Elm's allow-list: [{title, path, locations}]; [] when refused or not a postings list."""
    if not _wd_ok(b):
        return []
    gate(b["host"], 0.4)
    status, txt = workday._request("POST", f"https://{b['host']}/wday/cxs/{b['tenant']}/{b['site']}/jobs",
                                   {"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": text})
    if status != 200:
        return []
    try:
        posts = json.loads(txt).get("jobPostings")
    except (ValueError, AttributeError):
        return []
    return [{"title": str(p.get("title") or ""), "path": str(p.get("externalPath") or ""), "locations": str(p.get("locationsText") or "")}
            for p in posts if isinstance(p, dict) and workday.PATH_RE.match(str(p.get("externalPath") or ""))] if isinstance(posts, list) else []


def wd_detail(b: dict, path: str) -> tuple[int, dict | None]:
    if not _wd_ok(b) or not workday.PATH_RE.match(path or ""):
        return 0, None
    gate(b["host"], 0.4)
    status, txt = workday._request("GET", f"https://{b['host']}/wday/cxs/{b['tenant']}/{b['site']}{path}")
    try:
        d = json.loads(txt)
    except ValueError:
        d = None
    return status, d if isinstance(d, dict) else None


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))).strip()


def _day(v) -> str | None:
    """A date as YYYY-MM-DD from an ISO string or epoch milliseconds, else None."""
    try:
        if isinstance(v, (int, float)) and v > 0:
            return datetime.fromtimestamp(v / 1000, timezone.utc).date().isoformat()
        m = re.match(r"\d{4}-\d{2}-\d{2}", str(v or ""))
        return m.group(0) if m else None
    except (ValueError, OSError, OverflowError):
        return None


# ---- readers: each returns [{title, location, url, text, posted}] ----
def _greenhouse(b: dict) -> list[dict]:
    base = f"https://boards-api.greenhouse.io/v1/boards/{b['token']}/jobs"
    gate("greenhouse", GAP["greenhouse"])
    d = postings._get_json(base + "?content=true") or postings._get_json(base)
    if d is None:
        raise RuntimeError("board did not answer")
    return [{"title": j["title"], "location": (j.get("location") or {}).get("name"), "url": j["absolute_url"],
             "text": _plain(j.get("content") or ""), "posted": _day(j.get("updated_at") or j.get("first_published"))}
            for j in d.get("jobs") or [] if j.get("title") and j.get("absolute_url")]


def _lever(b: dict) -> list[dict]:
    gate("lever", GAP["lever"])
    d = postings._get_json(f"https://api.lever.co/v0/postings/{b['token']}?mode=json")
    if not isinstance(d, list):
        raise RuntimeError("board did not answer")
    out = []
    for j in d:
        if j.get("text") and j.get("hostedUrl"):
            cat = j.get("categories") or {}
            out.append({"title": j["text"], "location": cat.get("location"), "url": j["hostedUrl"],
                        "text": " ".join(x for x in (j.get("descriptionPlain"), j.get("additionalPlain")) if x),
                        "posted": _day(j.get("createdAt")), "commitment": cat.get("commitment") or ""})
    return out


def _ashby(b: dict) -> list[dict]:
    gate("ashby", GAP["ashby"])
    d = postings._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{b['token']}")
    if not isinstance(d, dict):
        raise RuntimeError("board did not answer")
    return [{"title": j["title"], "location": j.get("location"), "url": j["jobUrl"], "text": j.get("descriptionPlain") or "",
             "posted": _day(j.get("publishedAt")), "commitment": j.get("employmentType") or ""}
            for j in d.get("jobs") or [] if j.get("isListed", True) and j.get("title") and j.get("jobUrl")]


def _workday(b: dict) -> list[dict]:
    """Searched, not listed whole. The search keeps only candidates the title rules accept; for those the posting's own
    detail is read (text for the years / hourly rules, date, country when the location line is unclear)."""
    if not _wd_ok(b):
        raise RuntimeError("board not in Elm's seeds")
    found, answered = {}, 0
    for q in WD_QUERIES:
        try:
            for p in wd_search(b, q, 20):
                found[p["path"]] = p
            answered += 1
        except Exception:
            pass
    if not answered:
        raise RuntimeError("board did not answer")
    out = []
    for path, p in found.items():
        lab = entry.label(p["title"])
        if not lab or entry.location_us(p["locations"], p["title"]) is False:
            continue
        rec = {"title": p["title"], "location": p["locations"], "url": workday.public_url(b, path), "text": "", "posted": None}
        if lab == "entry" or entry.location_us(p["locations"], p["title"]) is None:   # text decides years / hourly; an unclear place is settled by the posting's country
            try:
                st, d = wd_detail(b, path)
                info = (d or {}).get("jobPostingInfo") or {}
                rec["text"] = _plain(info.get("jobDescription") or "")
                rec["posted"] = _day(info.get("startDate"))
                rec["url"] = workday.public_url(b, path, info)
                c = (info.get("country") or {}).get("descriptor") if isinstance(info.get("country"), dict) else None
                rec["country"] = c
            except Exception:
                pass
        out.append(rec)
    return out


READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "workday": _workday}


def _record(b: dict, j: dict, lab: str, fl: list[str], us, today: str) -> dict:
    rec = {"id": "elm-" + hashlib.sha1(postings.canonical(j["url"]).encode()).hexdigest()[:12],
           "company": b["company"], "title": j["title"].strip()[:160], "location": ((j.get("location") or "").strip()[:100] or None),
           "us": us, "kind": lab, "flags": fl, "ats": b["ats"], "url": re.sub(r"^http://", "https://", j["url"]),   # some boards (Block) list http:// links "posted": j.get("posted"), "seen": today,
           "sponsor": None, "sponsor_text": None, "sponsor_quote": None, "tier": TIER.get(b.get("size") or "")}
    s = sponsor_hit(b["company"])
    if s:
        rec["sponsor"] = {"lca_recent": s.get("recent_count"), "lca_total": s.get("certified"), "last_fy": s.get("recent_fy"), "source": "DOL LCA disclosure data"}
    text = j.get("text") or ""
    if text:
        verdict, quote = sponsor.text_signal(text)
        up, upq = sponsor.us_person_signal(text)           # citizenship / clearance: the employer cannot sponsor this role
        if up:
            verdict, quote = "no", upq
        if verdict:
            rec["sponsor_text"], rec["sponsor_quote"] = ("likely" if verdict == "yes" else "no"), quote
    return rec


def _read(b: dict, deadline: float):
    """(jobs, error) for one board; never raises."""
    if time.time() > deadline:
        return None, "skipped: time limit"
    try:
        return READERS[b["ats"]](b), None
    except Exception as e:                                    # one board down must not stop the others
        return None, str(e)[:120] or type(e).__name__


def pull() -> dict:
    """Read every seed board. Returns the summary dict (also written to _crawl.json)."""
    seeds = [b for b in json.loads(SEEDS.read_text())["boards"] if b.get("ats") in READERS]
    allow_workday(seeds)
    today, started = date.today().isoformat(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()
    rows, seen, per, failures = [], set(), {}, []
    with ThreadPoolExecutor(POOL) as ex:                      # readers run in parallel; records are built in seed order below
        results = list(ex.map(lambda b: _read(b, t0 + MAX_SECONDS), seeds))
    for b, (jobs, err) in zip(seeds, results):
        if err is not None:
            failures.append({"company": b["company"], "ats": b["ats"], "error": err})
            per[b["company"]] = {"read": 0, "kept": 0, "ats": b["ats"]}
            continue
        kept = 0
        for j in jobs:
            lab, fl = entry.classify(j["title"], j.get("text") or "")
            if not lab or j["url"] in seen:
                continue
            for name, rx in (("part_time", entry.PART_TIME), ("contract", entry.CONTRACT), ("seasonal", entry.SEASONAL)):
                if rx.search(j.get("commitment") or "") and name not in fl:
                    fl.append(name)                          # the board's own employment type
            if lab == "entry" and set(fl) & entry.DROP_FLAGS:
                continue
            us = entry.location_us(j.get("location"), j["title"])
            if us is None and j.get("country"):
                us = j["country"].lower().startswith("united states")
            if us is False:
                continue
            seen.add(j["url"])
            rows.append(_record(b, j, lab, fl, us, today))
            kept += 1
        per[b["company"]] = {"read": len(jobs), "kept": kept, "ats": b["ats"]}
    ok = len(per) - len(failures)
    if ok > 0 and rows:
        DATA.mkdir(exist_ok=True)
        tmp = ROLES.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False))
        tmp.replace(ROLES)                                   # atomic: a reader never sees half a file
    summary = {"started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seconds": round(time.time() - t0),
               "boards": len(per), "boards_ok": ok, "failures": failures, "roles": len(rows), "per_company": per, "wrote": bool(ok > 0 and rows)}
    if summary["wrote"]:
        try:                                                  # the public pool: drop roles whose page is gone (guarded, never fails the crawl)
            from elm import pool
            summary["pool_recheck"] = pool.recheck()
        except Exception as e:
            summary["pool_recheck"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    SUMMARY.write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    s = pull()
    k = [json.loads(ROLES.read_text())][0] if ROLES.exists() else []
    from collections import Counter
    c = Counter((r["kind"], r["us"]) for r in k)
    print(f"{s['boards_ok']}/{s['boards']} boards read, {len(s['failures'])} failed, {s['roles']} roles kept "
          f"(intern {sum(v for (kd, _), v in c.items() if kd == 'intern')}, entry {sum(v for (kd, _), v in c.items() if kd == 'entry')}) in {s['seconds']}s")
