"""Elm: is a role an internship or an entry-level professional role a new graduate can apply to? Title rules in code, no model.

label(title) -> "intern" | "entry" | None       flags(title, text) -> list      classify(title, text) -> (label, flags)
location_us(location, title) -> True | False | None   (None = cannot tell: kept, shown as unknown)
Precision over recall: a role that merely contains "analyst" or "associate" is not enough when the rest of the title
says it is hourly, frontline, clinical, a finance Associate (investment banking, private equity, credit ...) or senior.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import ats  # noqa: E402  (its _NON_US place list is reused)

INTERN = re.compile(r"\b(intern|interns|internship|internships|co-?op|summer (analyst|associate|scholar))\b", re.I)
STRONG = re.compile(r"\b(new[- ]?grads?|new graduates?|(recent|college|university|fresh) graduates?|fresh graduate|graduate (program|programme|analyst|engineer|scheme|rotation)|"
                    r"entry[- ]level|early[- ]career|early talent|campus|university (program|programme|hire|recruit)|junior|jr\.?|"
                    r"rotational|rotation program|development program(me)?|leadership development|management trainee|trainee|"
                    r"development representative|analyst program|(engineer|analyst|scientist|developer) (i|1)|associate (i|1)|level (i|1))\b", re.I)
ANALYST = re.compile(r"\banalysts?\b", re.I)
# "Associate" alone is too loose ("Clinical Associate", "Client Service Associate"): only these forms count.
ASSOC = re.compile(r"\bassociate (product manager|program manager|project manager|consultant|engineer|developer|software|data|analyst|scientist|"
                   r"marketing|brand|business|strategy|operations|auditor|accountant|buyer|planner|designer)\b|"
                   r"\b(marketing|brand|business|operations|supply chain|finance|financial|accounting|hr|human resources|product|program|data|project|"
                   r"communications|talent|recruiting|procurement|sourcing|materials management|merchandising|quality|regulatory|compliance|audit|risk|tax|"
                   r"solutions) associate\b|\bapm\b", re.I)
FIN_ASSOC = re.compile(r"\b(investment|investing|banking|banker|private equity|credit|capital markets|equity|asset management|portfolio|derivatives|"
                       r"advisory|transaction|wealth|m&a|buyout|venture|hedge|fund|funds|leveraged|restructuring|lending|real estate)\b", re.I)
APM_OK = re.compile(r"\b(associate|assistant) (product|program|project) manager\b|\bmanagement (trainee|development)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|manager|director|head|vp|vice president|president|chief|officer|partner|architect|"
                    r"expert|distinguished|fellow engineer|ii|iii|iv|v|2|3|4|executive|counsel|supervisor|specialist iii|consultant ii)\b", re.I)
# Frontline, trade, clinical and support titles: not what a new graduate with a degree is looking for, "entry level" or not.
BLUE = re.compile(r"\b(retail|store|warehouse|forklift|technician|operator|mechanic|driver|cashier|barista|cook|teller|nurse|pharmacy|pharmacist|"
                  r"clinical associate|client service|customer service|customer care|service associate|call cent(er|re)|field service|"
                  r"manufacturing|production associate|assembler|machinist|janitor|custodian|housekeep|team member|sales associate|"
                  r"sales representative|sales rep|representative|support assistant|care management|administrative|receptionist|"
                  r"skillbridge|registered|lab|laboratory|cleaning|production|professor|post-?doc|postdoctoral|apprentice|electrician|welder|lab assistant|phlebotom\w*|dispatcher)\b", re.I)
HOURLY = re.compile(r"\b(hourly|per hour|warehouse|non-exempt)\b|/\s?hr\b|\$\s?\d+(\.\d+)?\s?(/|per|an)\s?(hour|hr)\b", re.I)
PART_TIME = re.compile(r"\b(part[- ]time|pt\d*hrs?|\d+ ?hrs? ?/? ?(week|wk))\b", re.I)
SEASONAL = re.compile(r"\b(seasonal|temp|temporary|holiday)\b", re.I)
CONTRACT = re.compile(r"\b(contract(or|ors)?|contingent|fixed[- ]term|direct hire)\b", re.I)
MBA = re.compile(r"\bmba\b", re.I)
PHD = re.compile(r"\b(ph\.?d\.?|doctoral|doctorate)\b", re.I)
MBA_TEXT = re.compile(r"\b(currently|actively)? ?(enrolled|pursuing|pursuit|candidate)\b[^.]{0,60}\bmba\b|\bmba (student|candidate)s?\b", re.I)
PHD_TEXT = re.compile(r"\b(currently|actively)? ?(enrolled|pursuing|pursuit|candidate)\b[^.]{0,60}\b(ph\.?d\.?|doctoral|doctorate)\b|\bph\.?d\.? (student|candidate)s?\b", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2}\s*)?\+?\s*years?(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,4}?experience", re.I)

DROP_FLAGS = {"hourly", "part_time", "seasonal", "contract"}


def _title_flags(title: str) -> list[str]:
    t = title or ""
    out = []
    if MBA.search(t):
        out.append("mba_only")
    if PHD.search(t):
        out.append("phd_only")
    for name, rx in (("hourly", HOURLY), ("part_time", PART_TIME), ("seasonal", SEASONAL), ("contract", CONTRACT)):
        if rx.search(t):
            out.append(name)
    return out


def flags(title: str, text: str = "") -> list[str]:
    out = _title_flags(title)
    txt = text or ""
    if txt:
        for name, rx in (("mba_only", MBA_TEXT), ("phd_only", PHD_TEXT), ("hourly", HOURLY), ("part_time", PART_TIME)):
            if name not in out and rx.search(txt):
                out.append(name)
    return out


def label(title: str) -> str | None:
    """'intern', 'entry' or None, from the title alone."""
    t = title or ""
    if INTERN.search(t):
        return "intern"
    if _title_flags(t) and set(_title_flags(t)) & DROP_FLAGS:
        return None
    t2 = re.sub(r"development representative", "development rep", t, flags=re.I)
    if BLUE.search(t2):
        return None
    if SENIOR.search(t) and not APM_OK.search(t):
        return None
    if STRONG.search(t) or APM_OK.search(t) or ANALYST.search(t):
        return "entry"
    if ASSOC.search(t):
        return None if FIN_ASSOC.search(t) else "entry"
    return None


def min_years(text: str) -> int | None:
    ys = [int(m.group(1)) for m in YEARS.finditer(text or "") if int(m.group(1)) < 25]
    return min(ys) if ys else None


def classify(title: str, text: str = "") -> tuple[str | None, list[str]]:
    """(label, flags) with the posting text when there is one: 3+ years required demotes entry to None; hourly / part-time
    wording in the text drops an entry role (an internship keeps its flags but is not dropped for them)."""
    lab = label(title)
    fl = flags(title, text)
    if lab == "intern":
        if "hourly" in fl and "hourly" not in _title_flags(title):
            fl.remove("hourly")                       # interns are often paid by the hour: only a title that says so counts
        if "part_time" in fl and "part_time" not in _title_flags(title):
            fl.remove("part_time")
    elif lab == "entry":
        y = min_years(text)
        if (y is not None and y >= 3) or set(fl) & DROP_FLAGS:
            lab = None
    return lab, fl


_US_STRONG = re.compile(r"\b(united states( of america)?|u\.s\.a?\.?|usa|us)\b|\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
                        r"florida|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
                        r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
                        r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming)\b", re.I)
_US_CODE = re.compile(r"(?:,|\s-|\s)\s?(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b(?!\w)")
_MORE_NON_US = re.compile(r"\b(cambodia|phnom penh|dhaka|colombo|kathmandu|jakarta|bangkok|kuala lumpur|pakistan|karachi|lahore|johor|kulim|penang|panama|laakdal|warsaw|warszawa|krakow|wroclaw|makati|gurgaon|gurugram|haryana|karnataka|"
                          r"chennai|kolkata|delhi|telangana|maharashtra|tamil nadu|guangzhou|shenzhen|xiamen|suzhou|osaka|bucharest|budapest|sofia|"
                          r"vladivostok|moscow|kyiv|kiev|athens|cairo|lagos|nairobi|casablanca|riyadh|doha|abu dhabi|tel-aviv|haifa|caesarea|"
                          r"bogota|medell[ií]n|lima|quito|san jos[eé], costa rica|costa rica|guatemala|puerto rico|ho chi minh|hanoi|taipei|"
                          r"mys|sgp|ind|chn|gbr|irl|can|deu|fra|mex|bra|isr|pol|rou|nld|aus|jpn|kor|phl|twn|hkg|che|swe|are|sau|pak|vnm|tha|idn|"
                          r"serbia|belgrade|novi sad|vojvodina|luxembourg|russia|ukraine|kazakhstan|astana|chachoengsao|basel|geneva|lausanne|barcelona|valencia|treviso|rome|roma|turin|bologna|novomoskovsk|"
                          r"ita|esp|prt|nor|dnk|fin|aut|cze|hun|grc|tur|egy|zaf|nga|ken|arg|chl|col|per|ury|ecu|cri|gtm|dom|pan|pri|nzl|bgd|lka|npl|khm|mmr|"
                          r"(arabic|danish|norwegian|swedish|finnish|dutch|german|hebrew|turkish|french|japanese|korean|italian|portuguese|polish|czech|mandarin|cantonese|hindi) speak\w*|glasgow|edinburgh|manchester|dahisar|petaling|selangor)\b", re.I)


def location_us(location, title: str = "") -> bool | None:
    """True / False / None (cannot tell). 'Remote' alone is unknown, not US."""
    loc = (location or "").strip()
    both = f"{loc} | {title or ''}"
    if loc and _US_STRONG.search(loc):
        return True
    clean = re.sub(r"new mexico", "", both, flags=re.I)
    if ats._NON_US.search(clean) or _MORE_NON_US.search(clean):
        return False
    if loc and _US_CODE.search(loc):
        return True
    return None
