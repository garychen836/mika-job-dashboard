"""Mark employers that have sponsored work visas before, from public DOL data — the fact an international student needs first.

A role rarely says "we sponsor". But the U.S. Department of Labor publishes every Labor Condition Application (LCA) an
employer files for an H-1B, H-1B1 or E-3 worker (public domain, OFLC disclosure data). An employer with a *certified* LCA
has, at least once, taken the first legal step to hire someone who needs sponsorship. That is not a visa and not a promise,
but it is a real signal, far better than "unclear".

`sponsor_employers.json` (optional here; elm/data/sponsors.json is this repo's table) is a compact lookup — one entry per employer, the most recent certified fiscal year and
how many certified LCAs — derived from the DOL dataset by a DuckDB read of the public DOL files (the 7.5M-row source is
never kept in the repository). Matching is done here, in code (rule 5: no employer list goes to the model). `annotate`
upgrades a role's sponsorship from "unclear" to "likely" and attaches an honest note; it never overrides an explicit
"no" or a sponsorship the posting itself stated.
"""
import json
import os
import re

import ingest

FILE = ingest.RAW / "sponsor_employers.json"
# A role is called a strong sponsor ("likely") on the DOL signal only when the employer's recent certified-LCA count (the
# last ~2 fiscal years) clears this bar — not on a single old filing. Below it, the employer's history still shows as a
# fact on the card, but the role stays "unclear" (unknown), never asserted.
STRONG_RECENT = int(os.getenv("SPONSOR_STRONG", "50"))

# Legal-entity words dropped so a posting's company name matches the DOL employer name: "Palantir Technologies Inc." and
# "Palantir" become the same key. Brand words only; nothing that distinguishes two real firms.
_SUFFIX = re.compile(
    r"\b(incorporated|inc|llc|llc?p|corporation|corp|co|company|ltd|limited|lp|opco|holdings?|"
    r"technologies|technology|labs|laboratories|networks|group|business|markets|the)\b",
    re.I,
)


def norm(name: str) -> str:
    """A company name reduced to its brand, for matching a posting's employer to the DOL disclosure list."""
    s = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


_TABLE: dict | None = None


def table() -> dict:
    """{normalised employer name -> {"recent_fy": int, "certified": int}}. Read once."""
    global _TABLE
    if _TABLE is None:
        try:
            d = json.loads(FILE.read_text())
            _TABLE = {norm(k): v for k, v in (d.get("employers") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            _TABLE = {}
    return _TABLE


def lookup(company: str) -> dict | None:
    key = norm(company)
    return table().get(key) if key else None


# Above this many certified LCAs an employer is a high-volume filer (large outsourcing firms dominate the DOL counts); the
# page says so rather than letting a huge number read as a strong endorsement.
HIGH_VOLUME = 2000


def _note(hit: dict) -> str:
    fy, n = hit.get("recent_fy"), hit.get("certified")
    bits = [f"most recent FY{fy}"] if fy else []
    if n:
        bits.append(f"{n:,} certified")
    tail = f" ({', '.join(bits)})" if bits else ""
    base = (f"This employer has filed certified H-1B LCAs with the U.S. Department of Labor{tail}. "
            "An LCA is an application, not a visa and not a promise — it shows the employer has sponsored before. "
            "Treat it as an indicator only and confirm with the employer.")
    if n and n >= HIGH_VOLUME:
        base += " This is a high-volume filer, so the count alone says little about any one role."
    return base


# ---------- the posting's own words about sponsorship (pure code, verified verbatim; no model) ----------
# Precision over recall (the owner's rule): a wrong "no" hides a real opportunity, so only unmistakable, visa-anchored
# statements count. "sponsor" is ambiguous (a team, an event), so every pattern is tied to visa / work-authorization.
_REFUSE = re.compile(
    r"(?:\b(?:not|unable to|cannot|can ?not|can'?t|do not|don'?t|does not|doesn'?t|will not|won'?t|not able to|"
    r"not in a position to|are unable to|is unable to)\b[^.;:\n]{0,40}\bsponsor\w*)"
    r"|(?:\bno\b[^.;:\n]{0,15}(?:visa|work|employment|h-?1b)?\s*sponsorship)"
    r"|(?:authoriz\w*\s+to\s+work\b[^.;:\n]{0,70}\bwithout\b[^.;:\n]{0,25}sponsor\w*)"
    r"|(?:\bwithout\b[^.;:\n]{0,20}(?:visa\s+)?sponsorship)"
    r"|(?:\b(?:must|should)\b[^.;:\n]{0,45}\bwithout\b[^.;:\n]{0,20}sponsor\w*)"
    r"|(?:do(?:es)?\s+not\s+(?:require|need)\b[^.;:\n]{0,15}sponsorship)"
    r"|(?:not\s+(?:require|requiring|need|needing)\b[^.;:\n]{0,15}sponsorship)"
    # the negation after the noun: "visa sponsorship is not available for this position"
    r"|(?:sponsorship\b[^.;:\n]{0,25}\b(?:is|are|will be)?\s*not\s+(?:available|offered|provided|possible|supported))", re.I)
_OFFER = re.compile(
    r"(?:\b(?:will|can|do|does|happy to|able to|glad to|offers?|offering|provides?|providing)\b[^.;:\n]{0,20}"
    r"sponsor\w*[^.;:\n]{0,25}(?:visa|h-?1b|work|employment))"
    r"|(?:(?:visa|h-?1b|work|employment)\s+sponsorship(?![^.;:\n]{0,20}\bnot\b)[^.;:\n]{0,20}(?:available|offered|provided))"
    r"|(?:\bwe\b[^.;:\n]{0,15}sponsor\w*[^.;:\n]{0,20}(?:visa|h-?1b))"
    r"|(?:\bsponsor\w*\s+(?:a\s+)?(?:visa|h-?1b|work\s+visa)s?\b)", re.I)


def _verbatim(text: str, quote: str) -> bool:
    """The quote is literally on the page (whitespace- and case-insensitive). This mirrors screening._in exactly; it is
    copied here, not imported, so the build path never pulls in the model stack that screening carries."""
    return bool(quote.strip()) and re.sub(r"\s+", " ", quote.strip().lower()) in re.sub(r"\s+", " ", text.lower())


# A citizenship / clearance / export-control requirement is a DIFFERENT dimension from visa sponsorship: it rules out a
# non-citizen whatever their visa, so it must never be read as "the employer does not sponsor". Verbatim, visa-free
# context (US person / ITAR / EAR / clearance), precision over recall.
_US_PERSON = re.compile(
    r"(?:\bu\.?\s?s\.?\s+citizen(?:ship)?s?\b[^.;:\n]{0,40}(?:required|only|is required|must))"
    r"|(?:(?:must be|required to be|be a)\b[^.;:\n]{0,20}\bu\.?\s?s\.?\s+(?:citizen|national))"
    r"|(?:\bu\.?\s?s\.?\s+citizens?\s+or\s+(?:lawful\s+)?(?:permanent resident|green[\s-]?card|national)s?)"
    r"|(?:\bu\.?\s?s\.?\s+person\b[^.;:\n]{0,30}(?:itar|ear|export))"
    r"|(?:\b(?:itar|ear)\b[^.;:\n]{0,30}u\.?\s?s\.?\s+person)"
    r"|(?:(?:active|current|ability to (?:obtain|maintain))[^.;:\n]{0,25}(?:security|ts/sci|top secret|dod)?\s*clearance)"
    r"|(?:(?:security|ts/sci|top secret|dod)\s+clearance\b[^.;:\n]{0,20}(?:required|active))", re.I)


# A clause that only welcomes a clearance ("strongly encouraged to apply", "preferred") does not require one.
_NOT_REQUIRED = re.compile(r"\b(?:encourag\w*|preferred|a plus|nice to have|bonus|desirable|desired|advantage)\b", re.I)
# "sponsorship for an export license" is about export control, not a work visa.
_NOT_VISA = re.compile(r"\bexport\s+licen[cs]e|\bdeemed\s+export", re.I)


def us_person_signal(text: str) -> tuple[bool, str | None]:
    """Does the posting require U.S. citizenship / a security clearance (export control)? (bool, verbatim quote)."""
    if not text:
        return False, None
    for m in _US_PERSON.finditer(text):
        q = _sentence(text, m.start(), m.end())
        if _NOT_REQUIRED.search(q):
            continue
        return (True, q) if _verbatim(text, q) else (True, re.sub(r"\s+", " ", m.group(0)).strip())
    return False, None


def us_note(quote: str) -> str:
    """The honest reason for a us-person role: an identity / export-control wall, NOT an employer's refusal to sponsor."""
    return ("This role requires U.S. citizenship or permanent residency, or a security clearance (an identity / "
            f"export-control requirement), so the employer cannot sponsor a work visa for it: “{quote}”")


def _sentence(text: str, start: int, end: int) -> str:
    """The clause around a match, trimmed to a short honest quote (never empty: falls back to the matched words)."""
    a = max([x + 1 for x in (text.rfind(".", 0, start), text.rfind(";", 0, start), text.rfind("\n", 0, start)) if x != -1]
            + [start - 160, 0])
    b = min([x for x in (text.find(".", end), text.find("\n", end)) if x != -1] + [end + 160, len(text)])
    return (re.sub(r"\s+", " ", text[a:b]).strip(" .;\n")[:200]
            or re.sub(r"\s+", " ", text[start:end]).strip()[:200])


def text_signal(text: str) -> tuple[str | None, str | None]:
    """What the posting's own words say about visa sponsorship, read in code: ("no"|"yes"|None, quote).

    A refusal wins over an offer (the most specific, the employer's own limit). None when the words do not clearly say
    either — that stays unknown, never guessed."""
    if not text:
        return None, None
    for verdict, pat in (("no", _REFUSE), ("yes", _OFFER)):
        for m in pat.finditer(text):
            q = _sentence(text, m.start(), m.end())
            if _NOT_VISA.search(q):
                continue
            return verdict, (q if _verbatim(text, q) else re.sub(r"\s+", " ", m.group(0)).strip())
    return None, None


def annotate(jobs: list[dict]) -> int:
    """Set each role's sponsorship signal from the posting's own words and the employer's DOL history. Returns how many
    carry a DOL history fact.

    Precision over recall. For a role with no explicit signal yet ("unclear"): the posting's own words decide first — a
    verbatim refusal makes it "no" (+ quote), a verbatim offer makes it "likely" (+ quote); failing that, a *strong*
    recent DOL sponsor (recent certified count ≥ STRONG_RECENT) makes it "likely"; otherwise it stays unknown. A single
    old LCA never upgrades a role, and an explicit signal already on the role (its own "no"/"yes") is never overridden.
    Every matched employer also gets the factual sponsor_history badge, whatever the state. Mutates in place."""
    tbl = table()
    n = 0
    for j in jobs:
        hit = tbl.get(norm(j.get("company") or ""))
        if hit:
            j["sponsor_history"] = {
                "filed": True, "last_fy": hit.get("recent_fy"), "lca_count": hit.get("certified"),
                "recent_count": hit.get("recent_count"), "high_volume": bool((hit.get("certified") or 0) >= HIGH_VOLUME),
                "source": "DOL LCA",
            }
            j.setdefault("sponsorship_note", _note(hit))
            n += 1
        text = j.get("summary") or ""
        if not j.get("us_person_required"):
            up, upq = us_person_signal(text + " \n " + (j.get("sponsorship_quote") or ""))  # catalogue roles keep this text in the quote
            if up:
                # A citizenship / clearance requirement means the employer cannot sponsor a visa for the role, so it counts
                # as sponsorship="no" (reusing the sponsor-no filter) — but the reason shown is the identity wall, honestly,
                # and the separate us_person_required flag is kept.
                note = us_note(upq)
                j["us_person_required"], j["us_person_quote"], j["us_person_note"] = True, upq, note
                j["sponsorship"], j["sponsorship_quote"], j["sponsorship_note"] = "no", upq, note
        if (j.get("sponsorship") or "unclear") != "unclear" or j.get("sponsorship_quote"):
            continue                                              # a signal, or a quote already recorded for it, is kept as-is
        verdict, quote = text_signal(text)
        if verdict == "no":
            j["sponsorship"], j["sponsorship_quote"] = "no", quote
            j["sponsorship_note"] = f"The posting itself says it does not sponsor: “{quote}”"
        elif verdict == "yes":
            j["sponsorship"], j["sponsorship_quote"] = "likely", quote
            j["sponsorship_note"] = f"The posting itself says it sponsors: “{quote}”"
        elif hit and (hit.get("recent_count") or 0) >= STRONG_RECENT:
            j["sponsorship"] = "likely"                           # a strong recent DOL sponsor, company-level
    return n
