"""Receive the daily crawl from GitHub Actions: POST /api/admin/upload (the crawl runs outside the web server).

    mount(app)    adds the route. Disabled (not added, and 404 if the secret is later unset) unless env ELM_UPLOAD_SECRET is set.

Request: header X-Elm-Upload = the secret (constant-time compare), JSON body {"roles": [...], "events": [...], "crawl": {...}}
(any part may be absent, at least one present), at most 20 MB. Every role needs an https url, kind intern|entry, string title and
company (and, when roledata is importable, an original-company link: roledata.link_ok); every event a string title, an https url
and a start date. An empty list never replaces a file (an all-fail crawl keeps the old data). Files are written atomically into
the data dir and, through durable.write, to the database when ELM_DATABASE_URL is set. Code only, no model.
"""
import hmac, json, os, re
from datetime import date
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
MAX_ROLES, MAX_EVENTS = 60_000, 2_000
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _link_ok(role: dict) -> bool:
    try:
        import roledata
        return roledata.link_ok(role)
    except ImportError:
        u = str(role.get("url") or "")
        return u.startswith("https://") and "@" not in u.split("/")[2] if u.count("/") >= 3 else False


def _s(v, n=300) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= n


def validate(doc):
    """(clean {name: data}, "") or (None, reason). Returns the three files keyed by file name."""
    if not isinstance(doc, dict):
        return None, "body must be a JSON object"
    out = {}
    if "roles" in doc:
        roles = doc["roles"]
        if not isinstance(roles, list) or not roles or len(roles) > MAX_ROLES:
            return None, "roles must be a non-empty list"
        # A role in a bad shape is dropped, not the whole upload (2026-11-05: one company's 21 http:// links blocked 2,700
        # roles). But if most of the batch is bad, something is broken: refuse it and keep yesterday's roles.
        good = [r for r in roles if isinstance(r, dict) and _s(r.get("id"), 80) and _s(r.get("title"), 300) and _s(r.get("company"), 200)
                and r.get("kind") in ("intern", "entry") and _s(r.get("url"), 2000) and str(r["url"]).startswith("https://")
                and _link_ok(r)]
        if len(good) < len(roles) * 0.9:
            return None, f"{len(roles) - len(good)} of {len(roles)} roles have a bad shape (id, title, company, kind intern|entry, https url on a company or ATS host)"
        out["roles.json"] = good
        out["_dropped"] = len(roles) - len(good)
    if "events" in doc:
        ev = doc["events"]
        if isinstance(ev, dict):
            ev = ev.get("events")
        if not isinstance(ev, list) or len(ev) > MAX_EVENTS:
            return None, "events must be a list"
        for e in ev:
            if not (isinstance(e, dict) and _s(e.get("title"), 300) and _s(e.get("url"), 2000) and str(e["url"]).startswith("https://")
                    and isinstance(e.get("start"), str) and DAY.match(e["start"])):
                return None, "an event has a bad shape (title, https url, start date)"
        if ev:                                             # an empty events list keeps the file, like an all-fail crawl
            out["events.json"] = {"built": (doc["events"].get("built") if isinstance(doc["events"], dict) else None) or date.today().isoformat(), "events": ev}
    if "crawl" in doc:
        if not isinstance(doc["crawl"], dict):
            return None, "crawl must be an object"
        out["_crawl.json"] = doc["crawl"]
    return (out, "") if out else (None, "nothing to store")


def _write(name: str, data) -> None:
    try:
        import durable
        durable.write(name, data)                          # atomic file in the data dir + Postgres write-through when configured
        return
    except ImportError:
        pass
    d = Path(os.getenv("ELM_DATA_DIR") or Path(__file__).parent / "data")
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, d / name)


def mount(app) -> bool:
    """Add POST /api/admin/upload to a FastAPI app. Returns False (no route) when ELM_UPLOAD_SECRET is unset."""
    if not (os.getenv("ELM_UPLOAD_SECRET") or "").strip():
        return False
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.post("/api/admin/upload", include_in_schema=False)
    async def upload(request: Request):
        secret = (os.getenv("ELM_UPLOAD_SECRET") or "").strip()
        if not secret:
            return JSONResponse({"error": "not found"}, status_code=404)
        given = request.headers.get("x-elm-upload", "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            return JSONResponse({"error": "forbidden"}, status_code=401)
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BYTES:
                return JSONResponse({"error": "too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "bad length"}, status_code=400)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BYTES:
                return JSONResponse({"error": "too large"}, status_code=413)
        try:
            doc = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "not JSON"}, status_code=400)
        clean, why = validate(doc)
        if clean is None:
            return JSONResponse({"error": why}, status_code=422)
        dropped = clean.pop("_dropped", 0)
        for name, data in clean.items():
            _write(name, data)
        return {"ok": True, "dropped_roles": dropped,
                "stored": {n: (len(d) if isinstance(d, list) else len(d.get("events", [])) if n == "events.json" else 1) for n, d in clean.items()}}
    return True
