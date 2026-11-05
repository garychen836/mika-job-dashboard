"""Offline tests: event keyword filter and tags, no SOM / robots-disallowed hosts, the upload route. Run: python elm/tests/test_events.py"""
import json, os, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = Path(tempfile.mkdtemp())
os.environ["ELM_DATA_DIR"] = str(tmp)
os.environ["ELM_UPLOAD_SECRET"] = "test-secret-123"
os.environ.pop("ELM_DATABASE_URL", None)
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "backend"))
import events, upload  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ---- keyword filter and tags ----
assert events.is_career("Virtual Job & Internship Fair") and events.is_career("Mock interview night")
assert events.is_career("Founder Lunch & Learn") and events.is_career("H-1B lottery: attorney session")
assert not events.is_career("Spring concert in the courtyard") and not events.is_career("BA/MPP Open House")
assert not events.is_career("Lecture on trade", "the speaker will mention a founder and an interview once")     # broad words: title only
assert events.is_career("Lecture", "Career paths in public service")                                              # strong words: description too
assert "career-fair" in events.tags_for("SCS Career Expo") and "networking" in events.tags_for("Alumni networking mixer")
assert "interview" in events.tags_for("Mock Interview Night") and "visa" in events.tags_for("OPT and CPT basics")
assert events.tags_for("Career panel") and events.tags_for("Startups") == ["startups"]
ok = {"url": "https://careers.environment.yale.edu/events/x", "date": "2099-01-05", "start": "16:30", "end": "18:00", "title": "Alumni Careers Panel",
      "location": "Room 1", "source": "yse", "host": "YSE"}
e = events.shape(ok, "2026-11-05")
assert e and e["start"] == "2099-01-05T16:30" and e["end"] == "2099-01-05T18:00" and e["source"] == "yse" and e["id"].startswith("ev-")
assert events.shape({**ok, "date": "2020-01-01"}, "2026-11-05") is None                                            # past
assert events.shape({**ok, "url": "http://careers.environment.yale.edu/x"}, "2026-11-05") is None                  # not https
for bad in ("https://cdo.som.yale.edu/events/x", "https://groups.som.yale.edu/e", "https://events.yale.edu/event/x", "https://ocs.yale.edu/events/x"):
    assert events.shape({**ok, "url": bad}, "2026-11-05") is None, bad
assert len(events.build([ok, ok, {**ok, "url": "https://luma.com/abc", "date": "2099-01-04"}], "2026-11-05")) == 2       # de-duplicated, sorted
assert events.extra_events() == []

# ---- upload ----
role = {"id": "elm-1", "company": "Acme", "title": "Data Intern", "kind": "intern", "url": "https://boards.greenhouse.io/acme/jobs/1", "us": True}
ev = {"id": "ev-1", "title": "Career Fair", "start": "2099-01-05T10:00", "url": "https://careers.environment.yale.edu/e/1", "tags": ["career-fair"]}
off = FastAPI(); os.environ["ELM_UPLOAD_SECRET"] = ""
assert upload.mount(off) is False and TestClient(off).post("/api/admin/upload", json={}).status_code == 404         # disabled when unset
os.environ["ELM_UPLOAD_SECRET"] = "test-secret-123"
app = FastAPI(); assert upload.mount(app) is True
c = TestClient(app)
H = {"X-Elm-Upload": "test-secret-123"}
post = lambda body, h=H: c.post("/api/admin/upload", json=body, headers=h)
assert post({"roles": [role]}, {}).status_code == 401 and post({"roles": [role]}, {"X-Elm-Upload": "nope"}).status_code == 401
assert not (tmp / "roles.json").exists()
assert post([role]).status_code == 422 and post({}).status_code == 422 and post({"roles": []}).status_code == 422
assert post({"roles": [{**role, "url": "http://boards.greenhouse.io/acme/jobs/1"}]}).status_code == 422             # http
assert post({"roles": [{**role, "kind": "senior"}]}).status_code == 422 and post({"roles": [{**role, "title": 5}]}).status_code == 422
assert post({"roles": [{**role, "url": "https://www.linkedin.com/jobs/view/1"}]}).status_code == 422               # aggregator
assert post({"roles": [role], "events": [{**ev, "url": "http://x.edu/e"}]}).status_code == 422
assert not (tmp / "roles.json").exists(), "a rejected upload must write nothing"
assert c.post("/api/admin/upload", content=b"{not json", headers=H).status_code == 400
big = c.post("/api/admin/upload", content=b" " * (upload.MAX_BYTES + 1), headers=H); assert big.status_code == 413
r = post({"roles": [role], "events": [ev], "crawl": {"roles": 1}}); assert r.status_code == 200 and r.json()["ok"], r.text
assert json.loads((tmp / "roles.json").read_text())[0]["id"] == "elm-1"
assert json.loads((tmp / "events.json").read_text())["events"][0]["id"] == "ev-1" and json.loads((tmp / "_crawl.json").read_text()) == {"roles": 1}
assert not list(tmp.glob("*.tmp"))
post({"roles": [role], "events": []}); assert json.loads((tmp / "events.json").read_text())["events"], "an empty events list keeps the old file"
# ---- crawl: elm-side Workday allow-list, sponsor table first, politeness gate ----
import crawl  # noqa: E402
wd = {"ats": "workday", "host": "acme.wd5.myworkdayjobs.com", "tenant": "acme", "site": "ext"}
crawl.allow_workday([wd])
assert crawl._wd_ok(wd) and not crawl._wd_ok({**wd, "site": "other"}) and not crawl._wd_ok({**wd, "host": "evil.example.com"})
assert crawl.wd_search({**wd, "host": "evil.example.com"}, "intern") == [] and crawl.wd_detail({**wd, "tenant": "x"}, "/job/a") == (0, None)
crawl._SP = {"acme": {"name": "Acme Inc", "certified_recent_2fy": 120, "certified_total": 900, "last_fy": 2026}}
assert crawl.sponsor_hit("Acme, Inc.") == {"recent_count": 120, "certified": 900, "recent_fy": 2026}
crawl.gate("t", 0.01); crawl.gate("t", 0.01)
print("test_events: ok")
