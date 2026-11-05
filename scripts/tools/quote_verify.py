"""Check every quote in elm/static/quotes.json word for word against its Wikiquote page's raw wikitext, and that it
does not sit under a Misattributed / Disputed / Attributed heading. Code only, no model.

    .venv/bin/python scripts/tools/quote_verify.py [--write-bad OUT.json]

Prints one line per failing quote and a summary. Network: en.wikiquote.org (action=raw), one request per page.
"""
import json, re, sys, time, unicodedata
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import httpx  # noqa: E402

# Wikimedia refuses generic browser-like agents; its policy asks for a named tool with a project link.
UA = {"User-Agent": "MikaJobDashboard-quote-check/1.0 (https://mika-som.onrender.com; one request per page, run by hand)"}


def raw(title: str) -> str | None:
    r = httpx.get("https://en.wikiquote.org/w/index.php", params={"title": title, "action": "raw"}, headers=UA,
                  timeout=30, follow_redirects=False)
    return r.text if r.status_code == 200 else None

BAD_HEAD = re.compile(r"misattribut|disputed|attributed|about |quotes about|see also|external links", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"<[^>]+>|'''?|\[\[(?:[^|\]]*\|)?([^\]]*)\]\]|\{\{[^}]*\}\}", r"\1", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def sections(raw: str):
    """[(heading, text)] in order."""
    out, head, buf = [], "", []
    for line in raw.splitlines():
        m = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", line)
        if m:
            out.append((head, "\n".join(buf))); head, buf = m.group(2), []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def main():
    quotes = json.loads((ROOT / "elm/static/quotes.json").read_text())
    pages, bad = {}, []
    for q in quotes:
        title = unquote(q["url"].split("/wiki/", 1)[1].split("#")[0])
        if title not in pages:
            try:
                txt = raw(title)
                pages[title] = sections(txt) if txt else None
            except Exception:
                pages[title] = None
            time.sleep(0.3)
        secs = pages[title]
        if not secs:
            bad.append((q, "page unreadable")); continue
        n = norm(q["en"])
        where = [h for h, t in secs if n in norm(t)]
        if not where:
            bad.append((q, "not found verbatim"))
        elif all(BAD_HEAD.search(h or "") for h in where):
            bad.append((q, f"only under heading {where[0]!r}"))
    for q, why in bad:
        print(f"FAIL {why}: {q['who']}: {q['en'][:90]}")
    print(f"quotes {len(quotes)}, pages {len(pages)}, verified {len(quotes) - len(bad)}, failed {len(bad)}")
    if len(sys.argv) > 2 and sys.argv[1] == "--write-bad":
        Path(sys.argv[2]).write_text(json.dumps([dict(q, why=w) for q, w in bad], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
