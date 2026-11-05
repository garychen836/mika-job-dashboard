"""Public calendar readers taken from Mika's backend/calendar_io.py: parse an .ics feed, read an event page's
machine-readable block. Nothing per-user."""
import hashlib
import json
import os
import quopri
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo(os.getenv("LOCAL_TZ", "America/New_York"))


PAST_DAYS, AHEAD_DAYS = 30, 180  # the window kept from an import or a feed


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").replace("\\,", ",").replace("\\;", ";").strip()


def _when(value: str, params: dict) -> tuple[date, str | None]:
    """An ICS date or date-time as (local date, 'HH:MM' or None for all-day)."""
    if "T" not in value:
        return datetime.strptime(value[:8], "%Y%m%d").date(), None
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(TZ)
    elif params.get("TZID"):
        try:
            dt = dt.replace(tzinfo=ZoneInfo(params["TZID"])).astimezone(TZ)
        except Exception:
            pass  # an unknown zone name: keep the wall-clock time
    return dt.date(), dt.strftime("%H:%M")


def _repeats(first: date, rrule: str, lo: date, hi: date) -> list[date]:
    """Dates of a simple repeating event (daily or weekly, with INTERVAL, COUNT or UNTIL). Other rules: first date only."""
    rule = dict(p.split("=", 1) for p in rrule.split(";") if "=" in p)
    step = {"DAILY": 1, "WEEKLY": 7}.get(rule.get("FREQ", ""))
    if not step:
        return [first]
    step *= int(rule.get("INTERVAL", "1") or 1)
    until = hi
    if rule.get("UNTIL"):
        until = min(hi, datetime.strptime(rule["UNTIL"][:8], "%Y%m%d").date())
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else 400
    out, d = [], first
    while d <= until and len(out) < count:
        out.append(d)
        d += timedelta(days=step)
    return [d for d in out if d >= lo][:120]


def parse_ics(text: str) -> list[dict]:
    """VEVENTs in an .ics file as plain rows, limited to a window around today."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]  # a folded line continues the previous one
        else:
            lines.append(raw)
    lo, hi = date.today() - timedelta(days=PAST_DAYS), date.today() + timedelta(days=AHEAD_DAYS)
    rows, ev = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT" and ev is not None:
            if "DTSTART" in ev and ev.get("STATUS", ("", {}))[0] != "CANCELLED":
                day, start = _when(*ev["DTSTART"])
                end = _when(*ev["DTEND"])[1] if "DTEND" in ev and start else None
                text = lambda k: _unescape(quopri.decodestring(ev[k][0].encode()).decode("utf-8", "replace") if ev[k][1].get("ENCODING") == "QUOTED-PRINTABLE" else ev[k][0]) if k in ev else ""
                title = text("SUMMARY") or "(no title)"
                link = ev.get("URL", ("", {}))[0].strip()
                uid = ev.get("UID", (title, {}))[0]
                for d in _repeats(day, ev.get("RRULE", ("", {}))[0], lo, hi):
                    if lo <= d <= hi:
                        rows.append({
                            "id": "cal-" + hashlib.sha1(f"{uid}|{d}".encode()).hexdigest()[:12],
                            "uid": uid, "title": title, "date": d.isoformat(), "start": start, "end": end,
                            "location": text("LOCATION"), "category": guess_category(title),
                            # kept for school-wide calendars (ingest.load_feeds); a personal calendar ignores them
                            "url": link if link.lower().startswith("https://") else None, "notes": text("DESCRIPTION")[:400],
                            "host": ev.get("ORGANIZER", ("", {}))[1].get("CN", "").strip('" '), "types": ev.get("CATEGORIES", []),
                        })
            ev = None
        elif ev is not None and (m := re.match(r'((?:[^":]|"[^"]*")*):(.*)', line)):  # the first colon outside quotes
            head, value = m.groups()
            name, *params = re.findall(r'(?:[^";]|"[^"]*")+', head)
            if name.upper() == "CATEGORIES":  # may repeat: keep every value
                ev.setdefault("CATEGORIES", []).append(":".join(p.split("=", 1)[-1] for p in params[:1] + [value.strip()]))
            else:
                ev[name.upper()] = (value, dict(p.split("=", 1) for p in params if "=" in p))
    return rows


def guess_category(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ("coffee", "chat", "1:1", "catch up", "catch-up")):
        return "coffee_chat"
    if "interview" in t:
        return "interview"
    if any(k in t for k in ("dinner", "drinks", "party", "birthday", "happy hour")):
        return "social"
    if any(k in t for k in ("meeting", "sync", "call", "office hours")):
        return "meeting"
    return "other"


def _fetch(url: str) -> str:
    url = re.sub(r"^webcal://", "https://", url.strip(), flags=re.I)
    if not url.lower().startswith("https://"):
        raise ValueError("The link must start with https:// or webcal://")
    r = httpx.get(url, timeout=25, follow_redirects=True, headers={"User-Agent": "Mika/0.1"})
    r.raise_for_status()
    if "BEGIN:VCALENDAR" not in r.text[:2000]:
        raise ValueError("That link did not return a calendar (.ics) file.")
    return r.text


def unwrap_link(url: str) -> str:
    """Links copied out of e-mail are often wrapped by a mail scanner or a redirector (Outlook Safe Links, Google,
    Proofpoint). Return the address inside, so the event page itself is what gets read."""
    from urllib.parse import parse_qs, unquote, urlparse
    url = url.strip().strip("<>")
    for _ in range(3):
        u = urlparse(url)
        q = parse_qs(u.query)
        inner = None
        if u.hostname and u.hostname.endswith("safelinks.protection.outlook.com"):
            inner = (q.get("url") or [None])[0]
        elif u.hostname in ("www.google.com", "google.com") and u.path == "/url":
            inner = (q.get("q") or q.get("url") or [None])[0]
        elif u.hostname and "urldefense" in u.hostname and "__" in url:
            inner = url.split("__", 2)[1]
        if not inner:
            break
        url = unquote(inner) if "%" in inner and "://" not in inner else inner
    return url


def page_events(page: str) -> list[dict]:
    """Events a page describes about itself in schema.org JSON-LD (Luma, Eventbrite and most university calendars do).
    That block is written for machines, so the date and time are exact and no AI is needed."""
    out = []
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
            stack += d.get("@graph", []) if isinstance(d.get("@graph"), list) else []
            kind = d.get("@type")
            if not (kind == "Event" or (isinstance(kind, list) and "Event" in kind) or (isinstance(kind, str) and kind.endswith("Event"))) or not d.get("startDate"):
                continue
            def when(v):
                try:
                    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                except ValueError:
                    return None, None
                if t.tzinfo:
                    t = t.astimezone(TZ)
                return t.date().isoformat(), (t.strftime("%H:%M") if "T" in str(v) else None)
            day, start = when(d["startDate"])
            end = when(d.get("endDate"))[1] if d.get("endDate") else None
            loc = d.get("location")
            if isinstance(loc, list):
                loc = ", ".join(str(v) for v in loc) if all(isinstance(v, str) for v in loc) else (loc[0] if loc else None)
            place = (loc.get("name") or (loc.get("address") if isinstance(loc.get("address"), str) else (loc.get("address") or {}).get("streetAddress"))) if isinstance(loc, dict) else loc
            if isinstance(place, list):
                place = ", ".join(str(v) for v in place)
            if day and d.get("name"):
                out.append({"title": str(d["name"])[:160], "date": day, "start": start, "end": end, "location": str(place)[:120] if place else None,
                            "category": guess_category(str(d["name"]))})
    return out


def fetch_page(url: str) -> tuple[str, str | None, list[dict]]:
    """A public web page a student pasted the link to: its readable text, and the address of its own "add to
    calendar" file if it offers one (that file is exact, so it is used in preference to reading the text).
    Only https, only public addresses, at most 400 KB, and nothing is kept."""
    import html
    import ipaddress
    import socket
    from urllib.parse import urljoin, urlparse
    url = re.sub(r"^http://", "https://", unwrap_link(url), flags=re.I)
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("The link must start with https://")
    for info in socket.getaddrinfo(u.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("That address is not on the public internet.")
    import postings
    final, status, ctype, page = postings.safe_get(url, 400_000)     # every redirect hop is checked, not only the first address
    if status in (401, 403) or re.search(r"/(login|signin|sign-in|sso)\b|cas/login|idp/profile", final, re.I):
        raise ValueError("That page needs a sign-in or does not let programs read it, so Mika cannot open it. Copy the event's text, or take a screenshot, and drop it here instead.")
    if status in (404, 410):
        raise ValueError("That page was not found. Check the link, or the event may have been taken down.")
    if status >= 400:
        raise ValueError(f"That page answered {status}.")
    if "html" not in ctype and "text" not in ctype:
        raise ValueError("That link is not a web page.")
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    # An event's own date usually sits in a <header> inside the article, so headers stay. What goes is the site chrome
    # and, above all, the "related events" list at the bottom: its dates belong to other events.
    import sanitize
    body = re.sub(r"(?is)<(script|style|nav|footer|svg)[^>]*>.*?</\1>", " ", sanitize.clean_html(page, "event page")[0])
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
    text = re.split(r"(?i)\b(?:related (?:events|programs)|you may also like|more events|upcoming events|similar events)\b", text)[0]
    ics = re.search(r'href="([^"]*(?:download-ics|\.ics|/ical)[^"]*)"', page, re.I)
    link = urljoin(final, html.unescape(ics.group(1))) if ics else None
    if link and urlparse(link).hostname != urlparse(final).hostname:
        link = None                                   # only the page's own site
    return ((html.unescape(title.group(1)).strip() + ". ") if title else "") + text, link, page_events(page)
