"""Elm must never carry SOM-internal job-board data (docs/ELM.md rule 2): no "cdo", no som.yale.edu link, and no URL that
appears in data/raw/cdo_jobs.json, in elm/data/seeds.json or (when present) roles.json and events.json."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "elm" / "data"
CDO_FILE = ROOT / "data/raw/cdo_jobs.json"   # only in Mika's private repo
cdo = json.loads(CDO_FILE.read_text()) if CDO_FILE.exists() else []
urls = {str(r.get("url")).strip().rstrip("/").lower() for r in cdo if isinstance(r, dict) and r.get("url")}
checked = 0
for name in ("seeds.json", "roles.json", "events.json"):
    f = DATA / name
    if not f.exists():
        assert name != "seeds.json", "seeds.json is committed and must exist"
        continue
    text = f.read_text()
    checked += 1
    assert not re.search(r"\bcdo\b", text, re.I), f"{name} mentions cdo"
    assert "som.yale.edu" not in text.lower(), f"{name} links som.yale.edu"
    found = {u.rstrip("/").lower() for u in re.findall(r"https?://[^\s\"'\\<>]+", text)}
    bad = sorted(found & urls)
    assert not bad, f"{name} contains a URL from the SOM job board: {bad[:3]}"
for f in ROOT.rglob("*"):
    if f.is_file() and ".git" not in f.parts and "tests" not in f.parts and f.suffix in (".py", ".json", ".html", ".md", ".yml", ".mjs", ".txt", ".sh"):
        low = f.read_text(errors="ignore").lower()
        assert not any(h in low for h in ("cdo.som.yale.edu", "data/raw/cdo", "groups.som.yale.edu")), f"{f.relative_to(ROOT)} points at SOM-internal data"
print(f"elm no-cdo: ALL OK ({checked} files, repo scanned)")
