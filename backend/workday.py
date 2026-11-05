"""Workday job boards: read an employer's own public Workday site, and see when that posting is taken down.

Plain code, no model. Many employers publish on Workday, whose public job-search JSON needs no login. The boards come only from the
hand-checked seed table (here: elm/data/seeds.json, via elm/crawl.py) (never from a web page: rule 6). A role is matched to ONE posting only when the
titles are near-identical, the type (intern / full-time) agrees and exactly one posting qualifies; no confident match means nothing
is stored and nothing is closed. A match closes a role only on that posting's own detail answer (404/410 with Workday's error JSON).
Absence from a search never closes anything.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARDS_FILE = ROOT / "data" / "raw" / "workday_boards.json"
HOST_RE = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$")
PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PATH_RE = re.compile(r"^/job/[A-Za-z0-9_%.,()~+-]+(/[A-Za-z0-9_%.,()~+-]+)*$")
TIMEOUT, MAX_BYTES, MIN_SIM = 12, 1_500_000, 0.85
_SUFFIX = re.compile(r"\b(incorporated|systems|group|holdings|plc|lp|llp|sa|ag|gmbh|usa|us|international|global|the)\b")   


def company_key(name: str) -> str:
    """Company-name normalisation: lower case, punctuation and legal suffixes removed."""
    base = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\b(inc|llc|ltd|corp|corporation|company|co|the|com)\b\.?", " ", (name or "").lower())).strip()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", base)).strip() or base


def load_boards() -> dict[str, dict]:
    """{company key: board}, each board with a valid host, tenant and site. Names listed under "aliases" map to the same board."""
    try:
        raw = json.loads(BOARDS_FILE.read_text()).get("boards") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for key, b in raw.items():
        if not (isinstance(b, dict) and HOST_RE.match(str(b.get("host") or "")) and PART_RE.match(str(b.get("tenant") or "")) and PART_RE.match(str(b.get("site") or ""))):
            continue
        out[str(key)] = b
        for a in b.get("aliases") or []:
            out.setdefault(company_key(str(a)), b)
    return out


def board_for(company: str, boards: dict | None = None) -> dict | None:
    boards = load_boards() if boards is None else boards
    return boards.get(company_key(company))


def allowed(board: dict) -> bool:
    """Only a seed-file host of the Workday form is ever contacted."""
    try:
        host = str(board.get("host") or "")
        if not (HOST_RE.match(host) and PART_RE.match(str(board.get("tenant") or "")) and PART_RE.match(str(board.get("site") or ""))):
            return False
        return any(b.get("host") == host for b in load_boards().values())
    except Exception:
        return False


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, str]:
    """(status, text). No redirects, a timeout, a size cap. Raises on a network error."""
    import httpx
    hdr = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; MikaStudentApp/1.0)"}
    with httpx.Client(follow_redirects=False, timeout=TIMEOUT, headers=hdr) as c:
        with c.stream(method, url, json=body) as r:
            buf, n = [], 0
            for chunk in r.iter_bytes():
                n += len(chunk)
                if n > MAX_BYTES:
                    return r.status_code, ""
                buf.append(chunk)
            return r.status_code, b"".join(buf).decode("utf-8", "replace")


def search(board: dict, text: str, limit: int = 20) -> list[dict]:
    """Postings of one board for a search text: [{title, path, locations}]. [] when the board is refused or answers anything but a postings list."""
    if not allowed(board):
        return []
    url = f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}/jobs"
    status, txt = _request("POST", url, {"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": text})
    if status != 200:
        return []
    try:
        posts = json.loads(txt).get("jobPostings")
    except (ValueError, AttributeError):
        return []
    return [{"title": str(p.get("title") or ""), "path": str(p.get("externalPath") or ""), "locations": str(p.get("locationsText") or "")}
            for p in posts if isinstance(p, dict) and PATH_RE.match(str(p.get("externalPath") or ""))] if isinstance(posts, list) else []


def detail(board: dict, path: str) -> tuple[int, dict | None]:
    """(status, json) of one posting's detail. Raises on a network error; (0, None) when the board or path is refused."""
    if not allowed(board) or not PATH_RE.match(path or ""):
        return 0, None
    status, txt = _request("GET", f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}{path}")
    try:
        d = json.loads(txt)
    except ValueError:
        d = None
    return status, d if isinstance(d, dict) else None


def public_url(board: dict, path: str, info: dict | None = None) -> str:
    """The link a student opens: the posting's own externalUrl when it is on the board's host, else host/en-US/site/path."""
    u = str((info or {}).get("externalUrl") or "")
    if u.startswith(f"https://{board['host']}/"):
        return u
    return f"https://{board['host']}/en-US/{board['site']}{path}"


def is_gone(status: int, data: dict | None) -> bool:
    """True only for Workday's own 'not found' answer for a posting: 404 or 410 with its JSON error body (errorCode). A bare 404 page
    (a gateway, a block page) or anything else is not evidence."""
    return status in (404, 410) and isinstance(data, dict) and bool(data.get("errorCode"))


# ---- matching ----
FILLER = {"mba", "summer", "program", "programme", "the", "a", "an", "of", "and", "for", "in", "at", "to", "with", "new", "grad", "graduate", "2025", "2026", "2027", "2028"}
INTERN = {"intern", "interns", "internship", "internships", "coop", "co-op"}
FULL = {"fulltime", "full-time", "permanent"}


def _tokens(title: str) -> tuple[list[str], str]:
    """(distinctive tokens in order, type): type is 'intern', 'full' (said so) or '' (not said)."""
    t = re.sub(r"co-?op", "coop", (title or "").lower().replace("&", " and "))
    t = re.sub(r"full[\s-]*time", "fulltime", t)
    words = re.findall(r"[a-z0-9]+", t)
    kind = "intern" if any(w in INTERN for w in words) else "full" if any(w in {"fulltime", "permanent"} for w in words) else ""
    return [w for w in words if w not in FILLER and w not in INTERN and w not in {"fulltime", "permanent"}], kind


def _contiguous(small: list[str], big: list[str]) -> bool:
    return any(big[i:i + len(small)] == small for i in range(len(big) - len(small) + 1))


def titles_match(a: str, b: str) -> bool:
    """Near-identical titles: set similarity >= 0.85 after removing filler, or one distinctive part is a run of 3+ words inside the other
    that adds at most 2 words (a location or team tag). Both must agree on intern vs full-time when either says which."""
    ta, ka = _tokens(a)
    tb, kb = _tokens(b)
    if not ta or not tb:
        return False
    if (ka and not kb) or (kb and not ka) or (ka and kb and ka != kb):
        return False   # intern vs a title that does not say so (or says full-time) is not the same role
    sa, sb = set(ta), set(tb)
    if len(sa & sb) / len(sa | sb) >= MIN_SIM:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small) >= 3 and len(big) - len(small) <= 2 and _contiguous(small, big)


def queries(title: str) -> list[str]:
    """One or two search texts: the cleaned title, then its first five distinctive words."""
    toks, kind = _tokens(title)
    full = " ".join(toks + (["intern"] if kind == "intern" else []))
    short = " ".join(toks[:5])
    return [q for q in dict.fromkeys([full, short]) if q][:2]


def match(title: str, board: dict, search_fn=None) -> dict | None:
    """The one posting of the board that matches the role title, or None (no candidate, or more than one: not verifiable)."""
    search_fn = search_fn or search
    found: dict[str, dict] = {}
    for q in queries(title):
        for p in search_fn(board, q):
            if titles_match(title, p.get("title", "")):
                found[p["path"]] = p
    return next(iter(found.values())) if len(found) == 1 else None
