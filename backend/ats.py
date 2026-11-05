"""Open roles from company ATS public APIs, for the students the catalogue's feeds miss.

A job board built from a few feeds misses most small and
mid-size companies — exactly the sponsor-friendly firms an international student needs. But Greenhouse, Lever and Ashby
each publish a company's open roles as a plain JSON list for anyone to read, and Mika already trusts these three as
structured sources (see postings._from_system). So from a small seed list of company boards (here: elm/data/seeds.json, read by elm/crawl.py)
we pull their open roles straight into the shared pool, in pure code, with no model call.

Only the three systems' own list endpoints are fetched, at fixed hosts, following no redirects (postings._get_json); the
board tokens come from our own seed file, never from a web page, so there is no way for a page to point this at a private
address (rule 6). A board that does not resolve is skipped — nothing is invented (rule 7). Newest roles first, capped per
company and overall so the pool is not flooded. (pull()/load() are Mika's batch path; this repo uses the readers through elm/crawl.py.)
"""
import hashlib
import html
import json
import os
import re
import time

import ingest
import postings

# An ATS board is mostly deep engineering and IC roles an MBA would not target, and roles all over the world. Keep it to
# what this audience wants, in code (rule 8: no model call): a US or remote role whose title reads MBA-relevant, plus any
# internship (summer roles are worth showing whatever the title). H-1B is a US visa, so non-US roles do not belong here.
_NON_US = re.compile(r"\b(india|ireland|united kingdom|\buk\b|london|canada|toronto|vancouver|germany|berlin|munich|"
                     r"france|paris|spain|madrid|portugal|lisbon|poland|romania|singapore|japan|tokyo|korea|seoul|"
                     r"australia|sydney|melbourne|brazil|mexico|netherlands|amsterdam|israel|tel aviv|bangalore|"
                     r"bengaluru|hyderabad|mumbai|pune|gurgaon|noida|dublin|emea|apac|latam|philippines|manila|"
                     r"china|shanghai|beijing|hong kong|taiwan|sweden|stockholm|switzerland|zurich|united arab|dubai|"
                     r"montreal|quebec|ottawa|calgary|finland|helsinki|denmark|copenhagen|norway|oslo|belgium|brussels|"
                     r"italy|milan|austria|vienna|czech|prague|indonesia|jakarta|malaysia|kuala lumpur|thailand|bangkok|"
                     r"vietnam|new zealand|auckland|argentina|buenos aires|chile|santiago|colombia|bogota|south africa|"
                     r"nigeria|kenya|egypt|saudi|turkey|istanbul|nordics|dach|benelux|anz)\b", re.I)

# Only MBA business / management roles belong in this pool. Precision over recall: an engineering, IC-technical, clinical
# or hourly title is dropped even if it also reads like management ("Engineering Manager"), and a title that matches no
# business function and does not say "MBA" is dropped too. All in code, no model call.
_BLACK = re.compile(r"\b(software|engineer|engineering|developer|programmer|devops|sre|site reliability|back[- ]?end|"
                    r"front[- ]?end|full[- ]?stack|architect|hardware|firmware|embedded|\bqa\b|sdet|test engineer|security engineer|"
                    r"network|systems engineer|infrastructure|platform engineer|mobile|ios|android|designer|\bux\b|\bui\b|"
                    r"graphic|illustrator|animator|scientist|researcher|\bresearch\b|technician|machinist|electrician|"
                    r"mechanic|welder|warehouse|forklift|picker|packer|laborer|retail|cashier|barista|cook|\bchef\b|"
                    r"driver|courier|nurse|\brn\b|caregiver|phlebotomist|therapist|clinician|pharmacist|janitor|custodian|"
                    r"cleaner|maintenance|teacher|tutor|instructor|faculty|professor|paralegal|attorney|lawyer)\b", re.I)
# Each business function -> the TITLE_FUNCTION label it maps to. Checked in order; the first hit keeps the role and names
# its function. "manager" alone is deliberately NOT here (too broad) — only business functions qualify.
_WHITE = [
    (re.compile(r"\b(product marketing|growth marketing|brand|demand gen|lifecycle marketing|content marketing|marketing|communications|public relations|\bpr\b|comms)\b", re.I), "marketing"),
    (re.compile(r"\b(product manager|product management|\bpm\b|group product|principal product)\b", re.I), "product-management"),
    (re.compile(r"\b(program manager|program management|\btpm\b|technical program|project manager|chief of staff|operations|business operations|bizops|\bops\b|supply chain|logistics|procurement|fulfillment)\b", re.I), "operations"),
    (re.compile(r"\b(finance|financial|fp&a|accounting|controller|treasury|investor relations|investment|\bfp and a\b)\b", re.I), "finance"),
    (re.compile(r"\b(strateg|corporate strategy|business strategy|analytics|business analyst|insights)\b", re.I), "strategy"),
    (re.compile(r"\b(business development|bizdev|biz dev|partnerships?|alliances|corporate development|corp dev|\bm&a\b|go[- ]?to[- ]?market|\bgtm\b|sales strategy|sales planning|revenue strategy|commercial strategy)\b", re.I), "business-development"),
    (re.compile(r"\b(consult|advisory)\b", re.I), "strategy"),
    (re.compile(r"\b(general management|general manager|rotational|leadership development|\bmba\b|summer associate|management associate|business associate)\b", re.I), "leadership"),
]


def _business_function(title: str) -> str | None:
    t = title or ""
    for rx, fn in _WHITE:
        if rx.search(t):
            return fn
    return None


# Quota-carrying and support roles (account executives, SDRs, customer success / experience) are not what MBA programs
# recruit for, even though they sit under sales. Sales strategy / operations / GTM stay (they match a function above).
_SALES_IC = re.compile(r"\b(account executive|account manager|account director|sales executive|sales representative|"
                       r"sales rep|sales development|\bsdr\b|\bbdr\b|business development representative|inside sales|"
                       r"customer success|customer experience|customer support|support specialist|technical account|"
                       r"sales manager|sales associate|territory)\b", re.I)


def _relevant(title: str, location, rtype: str) -> bool:
    """Keep only US / remote MBA business roles: a title that reads engineering / IC / hourly / frontline sales is
    dropped; otherwise it must match a business function or say 'MBA'. A non-US place in the title counts too."""
    if (location and _NON_US.search(location)) or _NON_US.search(title or ""):
        return False
    if _BLACK.search(title or "") or _SALES_IC.search(title or ""):
        return False
    return bool(_business_function(title)) or bool(re.search(r"\bmba\b", title or "", re.I))

BOARDS = ingest.RAW / "ats_boards.json"   # not used here (elm/crawl.py reads elm/data/seeds.json)
FILE = ingest.RAW / "ats_jobs.json"
PER_COMPANY = int(os.getenv("ATS_PER_COMPANY", "8"))    # most roles kept from one company, newest first (spread across more firms)
TOTAL = int(os.getenv("ATS_TOTAL", "350"))              # most roles kept across all companies
LABEL = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}


_TIER = {"startup": "growth", "big": "big"}        # a growth-stage company buckets under Startups; a big one under Large companies


def _function(title: str) -> str | None:
    return _business_function(title)                # the same whitelist that kept the role names its function


def _record(company: str, title: str, location, url: str, ats: str, employment: str = "", text: str = "", size: str | None = None) -> dict:
    cu = postings.canonical(url)
    rec = {
        "id": "ats-" + hashlib.sha1(cu.encode()).hexdigest()[:12],
        "company": company, "title": (title or "").strip()[:160],
        "type": postings._kind(title or "", employment),
        "location": ((location or "").strip()[:100] or None),
        "function": _function(title), "industry": None, "paths": [], "tier": _TIER.get(size or ""), "status": "open",
        "deadline": None, "deadline_note": "See the posting", "sponsorship": "unclear",
        "summary": re.sub(r"\s+", " ", text or "").strip()[:600], "requirements": [],
        "source": f"{company} careers ({LABEL.get(ats, ats)})", "source_url": cu, "apply_url": cu,
    }
    if text:
        import sponsor                                 # read the posting's own words about sponsorship, in code (no model)
        verdict, quote = sponsor.text_signal(text)
        if verdict == "no":
            rec["sponsorship"], rec["sponsorship_quote"] = "no", quote
            rec["sponsorship_note"] = f"The posting itself says it does not sponsor: “{quote}”"
        elif verdict == "yes":
            rec["sponsorship"], rec["sponsorship_quote"] = "likely", quote
            rec["sponsorship_note"] = f"The posting itself says it sponsors: “{quote}”"
        up, upq = sponsor.us_person_signal(text)                  # citizenship / clearance: the employer cannot sponsor this role
        if up:
            note = sponsor.us_note(upq)
            rec["us_person_required"], rec["us_person_quote"], rec["us_person_note"] = True, upq, note
            rec["sponsorship"], rec["sponsorship_quote"], rec["sponsorship_note"] = "no", upq, note
    return rec


def _plain(markup: str) -> str:
    """Greenhouse sends each description as escaped HTML: unescape it, then drop the tags, for the sponsorship reader."""
    import sanitize
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sanitize.clean_html(html.unescape(markup or ""), "job description")[0])).strip()


def _greenhouse(company: str, token: str, size: str | None = None) -> list[dict]:
    # With content=true each job carries its description, so the posting's own sponsorship words can be read. A large
    # board can pass the 8 MB cap that way; then fall back to the plain list rather than lose the company.
    base = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    d = postings._get_json(base + "?content=true") or postings._get_json(base)
    jobs = sorted((d or {}).get("jobs") or [], key=lambda j: j.get("updated_at") or "", reverse=True)
    out = []
    for j in jobs:
        url = j.get("absolute_url")
        if j.get("title") and url:
            out.append(_record(company, j["title"], (j.get("location") or {}).get("name"), url, "greenhouse",
                               text=_plain(j.get("content") or ""), size=size))
    return out


def _lever(company: str, token: str, size: str | None = None) -> list[dict]:
    d = postings._get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    jobs = sorted(d or [], key=lambda j: j.get("createdAt") or 0, reverse=True) if isinstance(d, list) else []
    out = []
    for j in jobs:
        cat = j.get("categories") or {}
        url = j.get("hostedUrl")
        if j.get("text") and url:
            body = " ".join(x for x in (j.get("descriptionPlain"), j.get("additionalPlain")) if x)
            out.append(_record(company, j["text"], cat.get("location"), url, "lever", cat.get("commitment", ""), body, size))
    return out


def _ashby(company: str, token: str, size: str | None = None) -> list[dict]:
    d = postings._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    jobs = [j for j in (d or {}).get("jobs") or [] if j.get("isListed")]
    jobs.sort(key=lambda j: j.get("publishedAt") or "", reverse=True)
    out = []
    for j in jobs:
        url = j.get("jobUrl")
        if j.get("title") and url:
            out.append(_record(company, j["title"], j.get("location"), url, "ashby", j.get("employmentType", ""), j.get("descriptionPlain", ""), size))
    return out


_READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def _seed() -> list[dict]:
    try:
        return json.loads(BOARDS.read_text()).get("boards") or []
    except (OSError, ValueError):
        return []


def pull() -> str:
    """Read every seed board's open roles and write data/raw/ats_jobs.json. Network only, no model. Returns a summary."""
    rows, seen, firms = [], set(), 0
    for b in _seed():
        reader = _READERS.get((b.get("ats") or "").lower())
        if not reader or not b.get("token") or not b.get("company"):
            continue
        try:
            got = [r for r in reader(b["company"], b["token"], b.get("size")) if _relevant(r["title"], r["location"], r["type"])][:PER_COMPANY]
        except Exception:
            got = []                                   # one board being down must not stop the others (rule 7: skip, do not invent)
        kept = 0
        for r in got:
            u = r["apply_url"]
            if u in seen:
                continue
            seen.add(u)
            rows.append(r)
            kept += 1
        firms += 1 if kept else 0
        time.sleep(1.0 if b["ats"] == "lever" else 0.3)   # Lever asks for a second between calls; be polite to the others too
    rows = rows[:TOTAL]
    if rows:                                              # a 429/5xx or down board yields None -> that board is skipped; a
        FILE.write_text(json.dumps(rows, ensure_ascii=False))  # pull where every board fails keeps the last good pool, not an empty one
    return f"{len(rows)} roles from {firms} companies"


def load(known: list[dict]) -> list[dict]:
    """The pulled ATS roles as jobs-table records, deduped by canonical URL against the catalogue (and each other)."""
    if not FILE.exists():
        return []
    seen = {postings.canonical(j.get("apply_url") or j.get("source_url") or "") for j in known
            if (j.get("apply_url") or j.get("source_url"))}
    seen.discard("")
    out = []
    try:
        rows = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []
    for r in rows if isinstance(rows, list) else []:
        u = postings.canonical(r.get("apply_url") or r.get("source_url") or "")
        if not u or u in seen or not (r.get("company") and r.get("title")):
            continue
        seen.add(u)
        out.append(r)
    return out
