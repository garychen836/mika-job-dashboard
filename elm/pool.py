"""The public pool: roles a student's search found that the crawl does not have. Shared, no finder id, no model.

A URL joins only after code reads the company's own page again (postings.read: Greenhouse / Lever / Ashby data address, or the
page's schema.org JobPosting; Workday through workday.detail when the host is a hand-checked board in elm/data/seeds.json),
finds it live, and elm's own title rules say intern or entry. The record is built by crawl._record, so a pool role looks exactly
like a crawled one (sponsor facts from DOL data or the posting's own words). Mirrors backend/found_roles.add (STRUCTURED, canonical
URL de-duplication) without its Mika catalogue."""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import durable, postings, roledata, workday      # noqa: E402
from elm import entry                           # noqa: E402

FILE = "pool.json"
CAP = 400
PER_CALL = 12
STRUCTURED = {"the page's own data", "Greenhouse", "Lever", "Ashby"}     # as backend/found_roles.STRUCTURED


def _load() -> list[dict]:
    return [r for r in durable.read(FILE, {}).get("roles", []) if isinstance(r, dict) and r.get("id") and r.get("url")] \
        if isinstance(durable.read(FILE, {}), dict) else []


def size() -> int:
    return len(_load())


def _build(b: dict, j: dict, today: str):
    """(role, reason): crawl's record when the title rules keep it."""
    import crawl
    lab, fl = entry.classify(j["title"], j.get("text") or "")
    if lab not in ("intern", "entry"):
        return None, "not an internship or entry-level role"
    us = entry.location_us(j.get("location"), j["title"])
    if us is False:
        return None, "not in the United States"
    role = crawl._record(b, j, lab, fl, us, today)
    return (role, "") if roledata.link_ok(role) else (None, "the link is not the company's own posting")


def _workday(url: str, today: str):
    """(state, role|None, reason). state: live / gone / unknown / reject."""
    u = urlparse(url)
    host, i = (u.hostname or "").lower(), u.path.find("/job/")
    board = next((b for b in workday.load_boards().values() if b.get("host") == host), None)
    if not board or i < 0 or board["site"] not in u.path[:i].split("/"):
        return "reject", None, "not a known Workday board"
    path = u.path[i:]
    try:
        status, d = workday.detail(board, path)
    except Exception:
        return "unknown", None, "the board did not answer"
    if workday.is_gone(status, d):
        return "gone", None, "taken down"
    info = (d or {}).get("jobPostingInfo")
    if status != 200 or not isinstance(info, dict) or not info.get("title"):
        return "unknown", None, "unreadable"
    j = {"title": str(info["title"]), "location": str(info.get("location") or ""), "url": workday.public_url(board, path, info),
         "text": postings._plain(str(info.get("jobDescription") or ""), 6000), "posted": None}
    role, why = _build({"company": board["company"], "ats": "workday", "size": None}, j, today)
    return ("live", role, "") if role else ("reject", None, why)


def read_one(url: str, today: str | None = None):
    """(state, role|None, reason) for one https URL, by reading it again in code."""
    today = today or date.today().isoformat()
    if not (isinstance(url, str) and url.startswith("https://") and len(url) < 600):
        return "reject", None, "not an https link"
    if (urlparse(url).hostname or "").endswith(".myworkdayjobs.com"):
        return _workday(url, today)
    got = postings.read(url)
    rec = got.get("record")
    if not got.get("ok") or not rec:
        reason = got.get("reason") or ""
        if reason.startswith("The page could not be reached") or "sign-in" in reason:
            return "unknown", None, reason
        return ("gone" if rec is None and got.get("ok") else "reject"), None, reason or "no verifiable job data"
    if not (rec.get("verified") and rec.get("how") in STRUCTURED):
        return "reject", None, "the page has no verifiable job data"
    if rec.get("deadline") and rec["deadline"] < today:
        return "gone", None, "the deadline has passed"
    ats = {"Greenhouse": "greenhouse", "Lever": "lever", "Ashby": "ashby"}.get(rec.get("how"), "page")
    j = {"title": rec["title"], "location": rec.get("location"), "url": rec["url"], "text": got.get("text") or rec.get("summary") or "", "posted": None}
    role, why = _build({"company": rec["company"], "ats": ats, "size": None}, j, today)
    return ("live", role, "") if role else ("reject", None, why)


def add(urls: list[str], today: str | None = None) -> dict:
    """{"added": [roles], "already": [roles], "dropped": [{url, reason}]}. https only, at most PER_CALL links read."""
    today = today or date.today().isoformat()
    pool = _load()
    known = {postings.canonical(r["url"]): r for r in roledata.load("roles.json", "roles")["roles"] if r.get("url")}
    known.update({postings.canonical(r["url"]): r for r in pool})
    added, already, dropped, seen = [], [], [], set()
    for url in [u for u in (urls or []) if isinstance(u, str) and u.startswith("https://")][:PER_CALL]:
        cu = postings.canonical(url)
        if cu in seen:
            continue
        seen.add(cu)
        if cu in known:
            if roledata.link_ok(known[cu]):
                already.append({**known[cu], "source": "found"} if known[cu] in pool else known[cu])
            continue
        state, role, why = read_one(url, today)
        if state != "live" or not role:
            dropped.append({"url": url, "reason": why})
            continue
        role["id"] = "pool-" + role["id"].split("-", 1)[-1]
        cr = postings.canonical(role["url"])
        if cr in known:
            already.append(known[cr]); continue
        known[cr] = role; pool.append(role); added.append({**role, "source": "found"})
    if added:
        durable.write(FILE, {"built": today, "roles": sorted(pool, key=lambda r: r.get("seen") or "", reverse=True)[:CAP]})
    return {"added": added, "already": already, "dropped": dropped}


def recheck() -> dict:
    """Read every pool role again and drop the ones that are gone. A page that cannot be reached keeps its role."""
    pool, keep, dropped = _load(), [], 0
    for r in pool:
        state, _, _ = read_one(r["url"])
        if state in ("gone", "reject"):
            dropped += 1
        else:
            keep.append(r)
    if dropped:
        durable.write(FILE, {"built": date.today().isoformat(), "roles": keep})
    return {"checked": len(pool), "dropped": dropped}
