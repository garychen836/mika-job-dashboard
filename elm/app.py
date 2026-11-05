"""Mika Job Dashboard: FastAPI app. Run: uvicorn --app-dir elm app:app"""
from __future__ import annotations
import ipaddress, json, os, sys, threading, time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "backend"))      # Mika's postings / websearch / workday / sponsor
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

import durable, roledata as store, chat, interview, limits, elmstats, search, elmquotes as quotes   # noqa: E402  (elmquotes: backend/quotes.py is Mika's)

MAX_BODY = 20_000
STATIC = Path(__file__).parent / "static"
MIKA_IMAGES = Path(__file__).resolve().parent.parent / "frontend" / "mika"      # one copy of the owner's drawings: mounted read-only
MIKA_ICON = Path(__file__).resolve().parent.parent / "frontend" / "mika-icon.png"
app = FastAPI(title="Mika Job Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
import upload  # noqa: E402
upload.mount(app)   # POST /api/admin/upload, only when ELM_UPLOAD_SECRET is set (the daily GitHub crawl)

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    n = request.headers.get("content-length")
    # The daily crawl's upload is megabytes: its route checks the secret before reading and has its own cap (upload.MAX_BYTES).
    if n and n.isdigit() and int(n) > MAX_BODY and request.url.path != "/api/admin/upload":
        resp = JSONResponse({"error": "too large"}, status_code=413)
    else:
        resp = await call_next(request)
    return _sec(resp, request)


def _sec(resp, request):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Content-Security-Policy", CSP)
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if request.url.scheme == "https" or proto == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def client_ip(request: Request) -> str:
    """Same rule as Mika: Cloudflare's header wins, else the first public address from the right of X-Forwarded-For."""
    for part in [request.headers.get("cf-connecting-ip") or "", *reversed((request.headers.get("x-forwarded-for") or "").split(","))]:
        try:
            ip = ipaddress.ip_address(part.strip())
        except ValueError:
            continue
        if ip.is_global:
            return str(ip)
    return request.client.host if request.client else "?"


@app.on_event("startup")
def _restore():
    durable.restore()                       # a free host starts with an empty disk: bring back pool, usage, requests, counters


if MIKA_IMAGES.is_dir():
    app.mount("/mika", StaticFiles(directory=MIKA_IMAGES), name="mika")


@app.get("/mika-icon.png")
def icon():
    return FileResponse(MIKA_ICON, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
def index(request: Request):
    try:
        elmstats.visit(client_ip(request), request.headers.get("user-agent", ""))
    except Exception:
        pass
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/roles")
def api_roles(q: str = Query("", max_length=100), kind: str = Query("", max_length=10), sponsor_only: bool = False,
              startup_only: bool = False, us_only: bool = False, mba: bool = False, place: str = Query("", max_length=12),
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000)):
    return store.search_roles(q, kind, sponsor_only, startup_only, us_only, mba, limit, offset,
                              place if place in store.PLACES else "")


@app.get("/api/roles/exist")
def api_roles_exist(ids: str = Query("", max_length=4000)):
    """Which of these role ids are still listed (for the student's own tracking list). At most 200 ids."""
    want = [i for i in ids.split(",") if 0 < len(i) <= 80][:200]
    have = {r.get("id") for r in store.all_roles()["roles"]}
    return {"listed": [i for i in want if i in have]}


@app.get("/api/quote")
def api_quote(date: str = Query("", max_length=10)):
    try:
        day = quotes.date.fromisoformat(date) if date else None
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return quotes.for_day(day, store.load("events.json", "events")["events"])


@app.get("/api/events")
def api_events(q: str = Query("", max_length=100), limit: int = Query(30, ge=1, le=100)):
    return store.search_events(q, limit)


@app.get("/api/meta")
def api_meta():
    r, e = store.all_roles(), store.load("events.json", "events")
    src = r.get("sources")
    return {"roles": len(r["roles"]), "events": len(e["events"]), "built": r["built"],
            "sources": src if isinstance(src, int) else len(src) if isinstance(src, (list, dict)) else len({x.get("ats") for x in r["roles"]})}


class Turn(BaseModel):
    role: str = Field(max_length=10)
    text: str = Field(max_length=chat.MAX_MSG)


class Prefs(BaseModel):
    kind: str = Field("", max_length=10)
    chips: list[str] = Field(default_factory=list, max_length=24)
    majors: list[str] = Field(default_factory=list, max_length=3)


class ChatIn(BaseModel):
    message: str = Field(max_length=chat.MAX_MSG)
    prefs: Prefs | None = None
    history: list[Turn] = Field(default_factory=list, max_length=12)
    lang: str = Field("en", max_length=5)


@app.post("/api/chat")
async def api_chat(request: Request):
    raw = b""
    async for part in request.stream():          # a body without content-length is capped here
        raw += part
        if len(raw) > MAX_BODY:
            return JSONResponse({"error": "too large"}, status_code=413)
    try:
        body = ChatIn.model_validate_json(raw)
    except ValidationError as e:
        return JSONResponse({"error": "invalid", "detail": [x["loc"] for x in e.errors()]}, status_code=422)
    if not body.message.strip():
        raise HTTPException(400, "empty")
    lang = "zh" if body.lang.startswith("zh") else "en"
    why = chat.allow(client_ip(request))
    if why:
        return JSONResponse({"reply": chat.LIMIT_TEXT[lang], "limited": why}, status_code=429)
    reply = await chat.answer(body.message.strip(), [t.model_dump() for t in body.history], lang,
                              chat.clean_prefs(body.prefs.model_dump() if body.prefs else None))
    return reply


class MajorIn(BaseModel):
    id: str = Field(max_length=20)
    text: str = Field("", max_length=interview.MAX_MAJOR_TEXT)


class InterviewIn(BaseModel):
    history: list[Turn] = Field(default_factory=list, max_length=6)
    majors: list[MajorIn] = Field(default_factory=list, max_length=interview.MAX_MAJORS)
    lang: str = Field("en", max_length=5)


@app.post("/api/interview")
async def api_interview(body: InterviewIn, request: Request):
    lang = "zh" if body.lang.startswith("zh") else "en"
    if not body.history:                                   # the first question is fixed text: no model, no count
        return {"question": interview.FIRST[lang]}
    if interview.clean_history([t.model_dump() for t in body.history]) is None:
        raise HTTPException(400, "last turn must be the student's")
    if interview.is_final([t.model_dump() for t in body.history]):    # fixed questions cost nothing; only the profile step counts
        why = chat.allow(client_ip(request))               # the same counters as the chat
        if why:
            return JSONResponse({"busy": chat.LIMIT_TEXT[lang], "limited": why}, status_code=429)
    return await interview.step([t.model_dump() for t in body.history], lang, [m.model_dump() for m in body.majors])


# --- anonymous signals: counts per role per day, nothing about who ---
SIGNAL_PER_HOUR = 120
_sig_hits: dict[str, list[float]] = {}
_sig_lock = threading.Lock()


class SignalIn(BaseModel):
    role_id: str = Field(max_length=80)
    kind: str = Field(max_length=10)


def _signal_allow(ip: str) -> bool:
    now = time.time()
    with _sig_lock:
        if len(_sig_hits) > 5000:
            for k in [k for k, v in _sig_hits.items() if not v or now - v[-1] > 3600]:
                del _sig_hits[k]
        ts = [t for t in _sig_hits.get(ip, []) if now - t < 3600]
        if len(ts) >= SIGNAL_PER_HOUR:
            _sig_hits[ip] = ts
            return False
        ts.append(now)
        _sig_hits[ip] = ts
    return True


@app.post("/api/signal")
def api_signal(body: SignalIn, request: Request):
    if not _signal_allow(client_ip(request)):
        return JSONResponse({"error": "rate"}, status_code=429)
    if body.kind not in store.SIGNAL_KINDS or not store.role_exists(body.role_id):
        raise HTTPException(400, "invalid")
    store.record_signal(body.role_id, body.kind, date.today().isoformat())
    return {"ok": True}


# --- search for new roles, requests, interests, tracking counts, stats ---
class SearchIn(BaseModel):
    what: str = Field(max_length=search.MAX_WHAT)
    lang: str = Field("en", max_length=5)


@app.post("/api/search")
async def api_search(body: SearchIn, request: Request):
    lang = "zh" if body.lang.startswith("zh") else "en"
    res = await search.run(body.what, client_ip(request), lang)
    if res.get("error"):
        raise HTTPException(400, "empty")
    return res


class RequestIn(BaseModel):
    what: str = Field(max_length=search.MAX_REQ)
    kind: str = Field("", max_length=10)
    chips: list[str] = Field(default_factory=list, max_length=24)


@app.post("/api/request")
def api_request(body: RequestIn, request: Request):
    if not body.what.strip():
        raise HTTPException(400, "empty")
    if not limits.hit("request", client_ip(request), search.PER_IP_REQUEST):
        return JSONResponse({"error": "rate"}, status_code=429)
    return {"ok": search.add_request(body.what, body.kind, body.chips)}


class InterestsIn(BaseModel):
    chips: list[str] = Field(default_factory=list, max_length=24)
    kind: str = Field("", max_length=10)
    groups: list[str] = Field(default_factory=list, max_length=3)


@app.post("/api/interests")
def api_interests(body: InterestsIn, request: Request):
    if not limits.hit("interests", client_ip(request), 20, 3600):
        return JSONResponse({"error": "rate"}, status_code=429)
    elmstats.interest([c for c in dict.fromkeys(body.chips) if c in chat.CHIP_KW], body.kind if body.kind in ("intern", "entry", "both") else "",
                      [g for g in dict.fromkeys(body.groups) if g in elmstats.GROUPS])
    return {"ok": True}


class TrackIn(BaseModel):
    stage: str = Field(max_length=12)


@app.post("/api/track")
def api_track(body: TrackIn, request: Request):
    if body.stage not in elmstats.STAGES:
        raise HTTPException(400, "invalid")
    if not limits.hit("track", client_ip(request), 120, 3600):
        return JSONResponse({"error": "rate"}, status_code=429)
    elmstats.track(body.stage)
    return {"ok": True}


@app.get("/stats")
def stats_page(format: str = Query("", max_length=8)):
    if format == "json":
        return elmstats.summary()
    return FileResponse(STATIC / "stats.html", headers={"Cache-Control": "no-cache"})
