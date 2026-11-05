"""Web search for new roles (paid SEARCH_MODEL, through Mika's websearch.links) and the "tell us what you look for" requests.

Every cap is checked BEFORE any call, in this order: off switch, searches per day (all students), dollars per day, searches per
address per day. When one is hit, nothing is called and the student is told search is paused for today. Found links go through
pool.add, which reads each page again in code: a model's words never put a role in the pool."""
from __future__ import annotations
import asyncio, hashlib, os, re, sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import durable, limits, pool, usage, elmstats      # noqa: E402

MAX_WHAT = 200
MAX_REQ = 300
PER_IP_SEARCH, PER_IP_REQUEST = 3, 5
PAUSED = {"en": "Search is paused for today. Try the filters, or tell us what you are looking for and we will search later.",
          "zh": "今天的搜索已暂停。请先用筛选条件，或告诉我们你想找什么，我们稍后帮你搜索。"}
FAILED = {"en": "The search did not work this time. Try again later.", "zh": "这次搜索没有成功，请稍后再试。"}
INSTRUCTIONS = ("Find links to individual, currently open internship or entry-level (new graduate) job postings that match the request, "
                "in the United States unless the request says otherwise. Search at most twice. Give only direct links to a single posting "
                "on a company's own careers site or its recruiting system (Greenhouse, Lever, Ashby, Workday), never a job board, "
                "aggregator, search or list page, never a news article. Reply with one link per line and nothing else; at most 8 links.")
STOP = set("the and for with that this from have has are was were you your our can any all not but his her its who what when where which "
           "how why job jobs role roles looking look want need find work working please some more any about into out get new".split())


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def gate(ip: str | None, consume: bool = True) -> str:
    """'' if a search may go ahead (and the address's slot is taken), else why not: off / day / dollars / ip."""
    if os.getenv("ELM_SEARCH_OFF") == "1":
        return "off"
    if elmstats.today("searches") >= _int("ELM_SEARCHES_PER_DAY", 50):
        return "day"
    if usage.over_budget():
        return "dollars"
    if ip is not None:
        if not (limits.hit("search", ip, PER_IP_SEARCH) if consume else limits.peek("search", ip, PER_IP_SEARCH)):
            return "ip"
    return ""


async def run(what: str, ip: str | None, lang: str = "en") -> dict:
    """{"paused", "message"} when a cap is hit; else {"roles": cards (new first), "added": n, "known": m}."""
    what = " ".join(str(what).split())[:MAX_WHAT]
    if not what:
        return {"error": "empty"}
    why = gate(ip)
    if why:
        return {"paused": why, "message": PAUSED[lang], "roles": []}
    elmstats.count("searches")
    try:
        import websearch                                              # backend/websearch.py: the paid search call
        links, tok = await websearch.links(what, 8, INSTRUCTIONS)
    except Exception:
        return {"message": FAILED[lang], "roles": [], "failed": True}
    usage.record(tok.get("input", 0), tok.get("output", 0), "elm_search", tok.get("cached", 0))
    res = await asyncio.to_thread(pool.add, links)
    return {"roles": res["added"] + res["already"], "added": len(res["added"]), "known": len(res["already"])}


# ---- requests: what students looked for and did not find. Text and date only, never an address ----
FILE = "_requests.json"
KINDS = ("intern", "entry", "both", "")


def add_request(what: str, kind: str = "", chips: list | None = None) -> bool:
    import chat
    what = " ".join(str(what).split())[:MAX_REQ]
    if not what:
        return False
    with durable.lock():
        rows = durable.read(FILE, [])
        if len(rows) >= 2000:
            return False
        rows.append({"id": hashlib.sha1(f"{what}{len(rows)}{date.today()}".encode()).hexdigest()[:10], "date": date.today().isoformat(),
                     "what": what, "kind": kind if kind in KINDS else "", "chips": [c for c in (chips or []) if c in chat.CHIP_KW][:24],
                     "status": "pending"})
        durable.write(FILE, rows)
    return True


def _key(what: str) -> str:
    return " ".join(sorted({w.rstrip("s") for w in re.findall(r"[a-z0-9+#]{3,}", what.lower())} - STOP))   # same words, order and plurals ignored


def words_top(n: int = 10) -> list[list]:
    """The most common words across all requests (code count; words of 3+ letters, stop-words removed). Never a raw text."""
    c = Counter(w for r in durable.read(FILE, []) for w in set(re.findall(r"[a-z]{3,}", str(r.get("what", "")).lower())) if w not in STOP)
    return [[w, k] for w, k in c.most_common(n)]


def request_count() -> int:
    return len(durable.read(FILE, []))


def pending_groups() -> list[tuple[str, int, list[str]]]:
    """[(text, how many asked, row ids)] most-asked first."""
    g: dict[str, list[dict]] = {}
    for r in durable.read(FILE, []):
        if r.get("status") == "pending" and _key(r.get("what", "")):
            g.setdefault(_key(r["what"]), []).append(r)
    return sorted(((v[0]["what"], len(v), [r["id"] for r in v]) for v in g.values()), key=lambda t: -t[1])


def mark_done(ids: list[str], found: int) -> None:
    with durable.lock():
        rows = durable.read(FILE, [])
        for r in rows:
            if r.get("id") in ids:
                r["status"], r["found"] = "done", found
        durable.write(FILE, rows)


async def run_requests(max_n: int) -> list[dict]:
    """Search the most-asked pending requests within the same caps (no per-address cap); mark them done. Stops when paused."""
    out = []
    for what, n, ids in pending_groups()[:max(0, max_n)]:
        res = await run(what, None)
        if res.get("paused"):
            out.append({"stopped": res["paused"]}); break
        if res.get("failed"):
            continue
        mark_done(ids, res.get("added", 0)); out.append({"asked": n, "added": res.get("added", 0), "known": res.get("known", 0)})
    return out
