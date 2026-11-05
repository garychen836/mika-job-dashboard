"""Reading one job posting from a link.

A recruiting system's own data address is preferred (Greenhouse, Lever, Ashby): that is the posting itself, so the
fields are exact. Otherwise a page that describes itself in schema.org JobPosting JSON-LD is used. A page with neither
is returned as text, and whoever reads it (the chat assistant) must say that the fields were read, not given.
Only https, only public addresses, nothing is kept here. A page that does not let programs read it is dropped, not
worked around.
"""

import datetime
import hashlib
import html
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.robotparser import RobotFileParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

import sanitize

UA = {"User-Agent": "Mika/0.1 (Yale SOM student project)"}
MAX_BYTES = 600_000


def _public(url: str) -> str:
    url = re.sub(r"^http://", "https://", url.strip().strip("<>"), flags=re.I)
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("The link must start with https://")
    for info in socket.getaddrinfo(u.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("That address is not on the public internet.")
    return url


def safe_get(url: str, max_bytes: int = MAX_BYTES, timeout: float = 25) -> tuple[str, int, str, str]:
    """(final address, status, content type, text). Every hop of a redirect is checked like the first address: a
    public page must not be able to send this server to an address inside its own network. The body is read up to
    `max_bytes` and no further, so a huge page cannot fill the memory."""
    for _ in range(5):
        url = _public(url)
        with httpx.stream("GET", url, timeout=timeout, headers=UA, follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            raw = b""
            for chunk in r.iter_bytes():
                raw += chunk
                if len(raw) >= max_bytes:
                    break
            return str(r.url), r.status_code, r.headers.get("content-type", ""), raw[:max_bytes].decode(r.encoding or "utf-8", "replace")
    raise ValueError("That link redirects too many times.")


_ROBOTS_LOCK = threading.Lock()
_ROBOTS: dict = {}          # origin -> (when read, parser): one robots.txt read per site per few hours
ROBOTS_HOURS = 6


def robots_parser(status: int, text: str) -> RobotFileParser:
    """What a site's robots.txt says, by the usual rules: 200 is read; 404 and other plain 4xx mean no rules; 401, 403, 429
    and server errors mean the site does not want visitors right now, so nothing is allowed."""
    rp = RobotFileParser()
    if status == 200:
        rp.parse(text.splitlines())
    elif status in (401, 403, 429) or status >= 500:
        rp.disallow_all = True
    else:
        rp.allow_all = True
    return rp


def robots_allowed(url: str, agent: str = "Mika") -> bool:
    """May a program fetch this address? Reads the site's own /robots.txt (through safe_get) and keeps it for a few hours.
    A robots.txt that cannot be read at all counts as not allowed: nothing is fetched on a guess."""
    u = urlparse(url.strip())
    origin = f"{u.scheme}://{u.netloc.lower()}"
    with _ROBOTS_LOCK:                              # three readers of one site at once read its robots.txt once
        hit = _ROBOTS.get(origin)
        if not hit or time.time() - hit[0] > ROBOTS_HOURS * 3600:
            try:
                _, status, _, body = safe_get(origin + "/robots.txt", 200_000)
                rp = robots_parser(status, body)
            except Exception:
                rp = robots_parser(503, "")
            _ROBOTS[origin] = hit = (time.time(), rp)
    return hit[1].can_fetch(agent, url)


def canonical(url: str) -> str:
    """The address without tracking parameters or fragments, host in lower case: what identifies a posting."""
    u = urlparse(url.strip())
    keep = [(k, v) for k, v in parse_qsl(u.query) if k.lower() in ("gh_jid", "jid", "id", "jobid", "job_id", "reqid", "requisitionid")]
    path = u.path.rstrip("/") or "/"
    return urlunparse(("https", (u.hostname or "").lower(), path, "", urlencode(keep), ""))


def posting_id(url: str) -> str:
    return "my-" + hashlib.sha1(canonical(url).encode()).hexdigest()[:12]


def _kind(title: str, employment: str = "") -> str:
    low = f"{title} {employment}".lower()
    if "fellow" in low:
        return "fellowship"
    if re.search(r"\b(intern|internship|summer associate|summer analyst|co-op)\b", low):
        return "internship"
    return "full-time"


def _plain(text: str, n: int = 6000) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()[:n]


def _record(company: str, title: str, location: str | None, url: str, text: str, deadline: str | None = None,
            employment: str = "", how: str = "") -> dict:
    return {"company": company.strip()[:80], "title": title.strip()[:160], "location": (location or "").strip()[:100] or None,
            "type": _kind(title, employment), "deadline": deadline, "url": canonical(url), "summary": text[:600],
            "verified": True, "how": how}


def _get_json(url: str):
    """A recruiting system's data address: a fixed host, no redirects followed, at most 8 MB."""
    with httpx.stream("GET", url, timeout=25, headers=UA, follow_redirects=False) as r:
        if r.status_code != 200:
            return None
        raw = b""
        for chunk in r.iter_bytes():
            raw += chunk
            if len(raw) > 8_000_000:
                return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _from_system(url: str) -> dict | None:
    """Greenhouse, Lever and Ashby postings, read from the data address the system publishes for outside use."""
    u = urlparse(url)
    host, path = (u.hostname or "").lower(), u.path
    qs = dict(parse_qsl(u.query))
    m = re.match(r"^/([A-Za-z0-9_-]{1,60})/jobs/(\d{1,20})", path)
    co, jid = (m.group(1), m.group(2)) if m and (host == "greenhouse.io" or host.endswith(".greenhouse.io")) else (None, None)
    if co:
        d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs/{jid}")
        if d and d.get("title"):
            board = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{co}") or {}
            return _record(d.get("company_name") or board.get("name") or co.replace("-", " ").title(), d["title"],
                           (d.get("location") or {}).get("name"), d.get("absolute_url") or url, _plain(d.get("content", "")),
                           how="Greenhouse")
    m = re.match(r"^/([A-Za-z0-9_.-]{1,60})/([0-9a-f-]{36})", path)
    if m and host == "jobs.lever.co":
        d = _get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
        if d and d.get("text"):
            cat = d.get("categories") or {}
            return _record(m.group(1).replace("-", " ").title(), d["text"], cat.get("location"), d.get("hostedUrl") or url,
                           d.get("descriptionPlain", ""), employment=cat.get("commitment", ""), how="Lever")
    if m and host == "jobs.ashbyhq.com":
        d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
        job = next((j for j in (d or {}).get("jobs", []) if j.get("id") == m.group(2)), None)
        if job:
            return _record(m.group(1).replace("-", " ").title(), job["title"], job.get("location"), job.get("jobUrl") or url,
                           job.get("descriptionPlain", ""), employment=job.get("employmentType", ""), how="Ashby")
    return None


def _jsonld(page: str) -> dict | None:
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("@graph"), list):
                stack += d["@graph"]
            kind = d.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return d
    return None


def read(url: str) -> dict:
    """{"ok": bool, "reason": str, "record": {...} | None, "text": str}. `record["verified"]` is True when the fields
    came from the posting's own data; `text` is the page's words when they did not."""
    try:
        url = _public(url)
        system = _from_system(url)
        if system:
            return {"ok": True, "reason": "", "record": system, "text": system["summary"]}
        final, status, ctype, page = safe_get(url)
    except ValueError as e:
        return {"ok": False, "reason": str(e), "record": None, "text": ""}
    except (httpx.HTTPError, OSError) as e:
        return {"ok": False, "reason": f"The page could not be reached ({type(e).__name__}).", "record": None, "text": ""}
    if status in (401, 403) or re.search(r"/(login|signin|sign-in|sso)\b", final, re.I):
        return {"ok": False, "reason": "That page needs a sign-in or does not let programs read it.", "record": None, "text": ""}
    if status in (404, 410):
        return {"ok": False, "reason": "That page was not found; the posting may have been taken down.", "record": None, "text": ""}
    if status != 200 or "html" not in ctype:
        return {"ok": False, "reason": f"The page answered {status}.", "record": None, "text": ""}
    ld = _jsonld(page)
    shown, _ = sanitize.clean_html(page, "posting page")        # hidden text goes before any reader or model sees the page
    body = re.sub(r"(?is)<(script|style|nav|footer|svg)[^>]*>.*?</\1>", " ", shown)
    text = _plain(body, 6000)
    if ld and ld.get("title"):
        org = ld.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else "")
        loc = ld.get("jobLocation")
        loc = loc[0] if isinstance(loc, list) and loc else loc
        addr = (loc or {}).get("address") if isinstance(loc, dict) else None
        place = ", ".join(x for x in ((addr or {}).get("addressLocality"), (addr or {}).get("addressRegion")) if x) if isinstance(addr, dict) else None
        until = str(ld.get("validThrough") or "")[:10]
        emp = ld.get("employmentType")
        rec = _record(str(company or urlparse(final).hostname or ""), str(ld["title"]), place, final, _plain(ld.get("description", ""), 600) or text,
                      deadline=until if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until) else None,
                      employment=" ".join(emp) if isinstance(emp, list) else str(emp or ""), how="the page's own data")
        return {"ok": True, "reason": "", "record": rec, "text": text}
    if len(text) < 200:
        return {"ok": False, "reason": "The page has no readable text (it may load its content with scripts).", "record": None, "text": ""}
    return {"ok": True, "reason": "", "record": None, "text": text, "url": canonical(final)}


# ---------- is a listing still what its source says? (no AI: the page is fetched and compared in code) ----------

_WORD = re.compile(r"[a-z0-9]{4,}")


def check_page(url: str | None, title: str, date: str | None = None, start: str | None = None) -> dict:
    """Open a listing's source link and say what holds: {opens, reason, host, title, date, time}, the last three
    "found" / "not_found" / "not_checked". Only yes-or-no answers leave this function, never the page's text, so it
    is safe to hand to an assistant that has tools. "not_found" is weak evidence (some pages fill in their details
    with script); a link that does not open is the strong signal."""
    out = {"opens": False, "status": 0, "reason": "", "host": "", "title": "not_checked", "date": "not_checked", "time": "not_checked"}
    if not url:
        return out | {"reason": "This listing has no source link."}
    try:
        final, status, _, page = safe_get(url, 600_000)
    except Exception as e:
        why = str(e)
        return out | {"host": urlparse(url).hostname or "", "reason": "The site's security certificate does not match its address." if "CERTIFICATE" in why
                      else "The site did not answer." if "Connect" in type(e).__name__ or "Timeout" in type(e).__name__ else why[:120]}
    out["host"], out["status"] = urlparse(final).hostname or "", status
    text = _plain(sanitize.clean_html(page, "page check")[0], 400_000).lower()
    if status != 200:
        return out | {"reason": {404: "The page no longer exists (404).", 403: "The site refuses automated visits (403), so it could not be checked.",
                                 401: "The page needs a sign-in."}.get(status, f"The site answered with status {status}.")}
    if re.search(r"/(login|signin|sso|cas)(/|$)", urlparse(final).path.lower()) or (urlparse(final).hostname or "").startswith(("secure.", "cas.")):
        return out | {"reason": "The link leads to a sign-in page, so it could not be checked."}
    out["opens"] = True
    words = set(_WORD.findall(title.lower()))
    out["title"] = "found" if words and sum(w in text for w in words) / len(words) >= 0.8 else "not_found"
    if date:
        try:
            d = datetime.date.fromisoformat(date)
            month, mon = d.strftime("%B").lower(), d.strftime("%b").lower()
            forms = [f"{month} {d.day}", f"{mon} {d.day}", f"{mon}. {d.day}", f"{d.day} {month}", f"{d.day} {mon}", d.isoformat(),
                     f"{d.month}/{d.day}", f"{d.month:02d}/{d.day:02d}", d.strftime("%Y%m%d")]
            out["date"] = "found" if any(re.search(r"(?<![0-9])" + re.escape(f) + r"(?![0-9])", text) for f in forms) else "not_found"
        except ValueError:
            pass
    if start and re.fullmatch(r"\d\d:\d\d", start):
        h, m = int(start[:2]), start[3:]
        h12 = h % 12 or 12
        forms = [f"{h12}:{m}", f"{h:02d}:{m}"] + ([f"{h12} pm", f"{h12}pm", f"{h12} p.m", f"{h12} am", f"{h12}am", f"{h12} a.m"] if m == "00" else [])
        out["time"] = "found" if any(re.search(r"(?<![0-9:])" + re.escape(f), text) for f in forms) else "not_found"
    return out
