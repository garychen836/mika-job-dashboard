"""The daily crawl's upload is megabytes; every other route keeps the 20 KB cap. Offline, no network.
Why: 2026-11-05, the first live upload of 2,700 roles got 413 from the app-wide size guard, so the live page stayed empty;
the tests never sent an upload bigger than the guard. Prints "elm upload size: ALL OK"."""
import json, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = Path(tempfile.mkdtemp())
os.environ.update(ELM_DATA_DIR=str(tmp), ELM_UPLOAD_SECRET="test-upload-secret-not-real")
for k in ("ELM_DATABASE_URL", "ELM_CHAT_MODEL", "PUBLIC_MODEL", "OPENROUTER_API_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE.parent))
from fastapi.testclient import TestClient  # noqa: E402
import app as A  # noqa: E402

c = TestClient(A.app)
base = json.loads((HERE / "fixture_roles.json").read_text())
base = base["roles"] if isinstance(base, dict) else base
roles = [dict(r, id=f"{r['id']}-{i}", url=f"{r['url'].rstrip('/')}-{i}") for i in range(300) for r in base]          # about 3,000 roles, well over 20 KB
body = json.dumps({"roles": roles, "events": [], "crawl": {"boards": 1}}).encode()
assert len(body) > 20_000 * 20, len(body)
hdr = {"Content-Type": "application/json"}
r = c.post("/api/admin/upload", content=body, headers={**hdr, "X-Elm-Upload": "wrong"})
assert r.status_code in (401, 403), r.status_code                                   # no secret: refused before reading
r = c.post("/api/admin/upload", content=body, headers={**hdr, "X-Elm-Upload": "test-upload-secret-not-real"})
assert r.status_code == 200, (r.status_code, r.text[:200])
assert c.get("/api/meta").json()["roles"] > 2000, c.get("/api/meta").json()
r = c.post("/api/chat", content=b'{"message":"' + b"a" * 30000 + b'"}', headers=hdr)
assert r.status_code == 413, r.status_code                                          # every other route keeps the cap
# a few bad roles are dropped, not the whole batch (2026-11-05: Block's 21 http:// links got the first real upload 422)
H = {**hdr, "X-Elm-Upload": "test-upload-secret-not-real"}
few_bad = roles[:200] + [dict(roles[0], id=f"bad-{i}", url=roles[0]["url"].replace("https://", "http://") + f"-x{i}") for i in range(5)]
r = c.post("/api/admin/upload", content=json.dumps({"roles": few_bad}).encode(), headers=H)
assert r.status_code == 200 and r.json()["dropped_roles"] == 5 and r.json()["stored"]["roles.json"] == 200, r.text[:300]
most_bad = roles[:5] + [dict(roles[0], id=f"bad-{i}", url=f"http://example.com/{i}") for i in range(50)]
r = c.post("/api/admin/upload", content=json.dumps({"roles": most_bad}).encode(), headers=H)
assert r.status_code == 422, r.status_code                                          # a broken crawl keeps yesterday's roles
assert c.get("/api/meta").json()["roles"] == 200
print("elm upload size: ALL OK")
shutil.rmtree(tmp, ignore_errors=True)
