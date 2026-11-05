"""No module in elm/ may share a name with one in backend/: elm adds backend/ to sys.path, so the first one found wins
and the other is silently shadowed. Why: 2026-11-05, Mika's new backend/quotes.py replaced elm/quotes.py in the
dashboard. Prints "elm module names: ALL OK"."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
clash = sorted({p.name for p in (ROOT / "elm").glob("*.py")} & {p.name for p in (ROOT / "backend").glob("*.py")} - {"__init__.py"})
assert not clash, f"rename these elm modules (e.g. elm<name>.py): {clash}"
print("elm module names: ALL OK")
