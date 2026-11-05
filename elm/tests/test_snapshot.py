"""The published data snapshot (elm/snapshot.py), offline: _get is replaced, so no network.
Why: 2026-11-05, a free Render instance slept 30 minutes after an upload and woke with no roles. Prints "elm snapshot: ALL OK"."""
import gzip, json, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = Path(tempfile.mkdtemp())
os.environ.update(ELM_DATA_DIR=str(tmp), ELM_SNAPSHOT_URL="https://github.com/example/repo/releases/download/data-latest/elm-data.json.gz")
os.environ.pop("ELM_DATABASE_URL", None)
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent / "backend")]
import snapshot  # noqa: E402

base = json.loads((HERE / "fixture_roles.json").read_text())
roles = base["roles"] if isinstance(base, dict) else base
good = gzip.compress(json.dumps({"roles": roles, "events": [], "crawl": {"boards": 1}}).encode())
calls = []
snapshot._get = lambda u: (calls.append(u), good)[1]

assert snapshot.load() == len(roles) and (tmp / "roles.json").exists()                  # empty disk: filled from the snapshot
assert json.loads((tmp / "_crawl.json").read_text()) == {"boards": 1}
(tmp / "roles.json").write_text(json.dumps(roles[:2]))
assert snapshot.load() == 0 and len(json.loads((tmp / "roles.json").read_text())) == 2      # data on disk is never replaced
assert len(calls) == 1                                                                  # and nothing was fetched for it

(tmp / "roles.json").unlink()
snapshot._get = lambda u: gzip.compress(json.dumps({"roles": [{"id": "x", "title": "t", "company": "c", "kind": "intern", "url": "http://evil.example/x"}]}).encode())
assert snapshot.load() == 0 and not (tmp / "roles.json").exists() and "bad shape" in snapshot.status["error"]   # same checks as an upload
snapshot._get = lambda u: b"not json"
assert snapshot.load() == 0 and not (tmp / "roles.json").exists()

# the real fetcher refuses any host but GitHub's, before any request
import importlib
importlib.reload(snapshot)
for bad in ("https://evil.example/elm-data.json.gz", "http://github.com/x", "https://github.com.evil.example/x", "file:///etc/passwd"):
    try:
        snapshot._get(bad)
        raise AssertionError(bad)
    except ValueError as e:
        assert "not allowed" in str(e), (bad, e)

os.environ["ELM_SNAPSHOT_URL"] = "off"
assert snapshot.load() == 0                                                             # tests and the red team stay offline
print("elm snapshot: ALL OK")
shutil.rmtree(tmp, ignore_errors=True)
