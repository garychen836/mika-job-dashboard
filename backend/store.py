"""A durable copy of everything that must outlive a restart.

Mika works on plain files under data/. On a host whose disk is wiped whenever the process restarts (Render's free
web service is one: it restarts on every deploy and after every sleep) those files are only a working copy.
With DATABASE_URL set (Postgres), every file saved through users.write() or keep() is also written as one row, and
restore() writes all the rows back to disk when the process starts. MIKA_STORE=sqlite:///some/file.db does the same
against a SQLite file, which is how this is tested without a database server.
With neither set this module does nothing and the files are the only copy (a laptop).

One table, one row per file:  mika_files(scope, name, body, updated)
  scope "user:<id>"   that student's files: profile.json, feedback.json, calendar.json, ...
  scope "shared"      files everyone shares: the personal-code and account indexes, token use, pulled public data

A failure here never fails a request. The file has already been written, the error is printed, and the next save
of that file tries again.
"""

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

URL = (os.getenv("DATABASE_URL") or os.getenv("MIKA_STORE") or "").strip()
_lock = threading.Lock()
_conn = None
_mark = "?"

SCHEMA = ("CREATE TABLE IF NOT EXISTS mika_files (scope TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, "
          "updated TEXT NOT NULL, PRIMARY KEY (scope, name))")


def enabled() -> bool:
    return bool(URL)


def _connect():
    global _conn, _mark
    if URL.startswith("postgres"):
        import psycopg  # only needed where a Postgres database is configured
        _conn, _mark = psycopg.connect(URL, autocommit=True), "%s"
    else:
        path = Path(re.sub(r"^sqlite:///", "", URL))
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn, _mark = sqlite3.connect(path, check_same_thread=False, isolation_level=None), "?"
    _conn.execute(SCHEMA)


def _run(sql: str, args: tuple = (), fetch: bool = False):
    """One statement, written with ? marks. A dropped connection is reopened once."""
    if not enabled():
        return [] if fetch else None
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None:
                    _connect()
                cur = _conn.execute(sql.replace("?", _mark), args)
                return cur.fetchall() if fetch else None
            except Exception as e:
                _drop_connection()
                if attempt == 2:
                    import opslog
                    opslog.note("database", f"{type(e).__name__}: {e}", "error")
                    return [] if fetch else None


def _drop_connection() -> None:
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def keep(scope: str, name: str, body: str) -> None:
    _run("INSERT INTO mika_files (scope, name, body, updated) VALUES (?, ?, ?, ?) "
         "ON CONFLICT (scope, name) DO UPDATE SET body = excluded.body, updated = excluded.updated",
         (scope, name, body, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def drop(scope: str, name: str | None = None) -> None:
    if name is None:
        _run("DELETE FROM mika_files WHERE scope = ?", (scope,))
    else:
        _run("DELETE FROM mika_files WHERE scope = ? AND name = ?", (scope, name))


def fetch(scope: str, name: str | None = None) -> list[tuple[str, str]]:
    """The stored copy of one file, or of every file in a scope: [(name, body)]. Empty when there is no store."""
    if name is None:
        return [tuple(r) for r in _run("SELECT name, body FROM mika_files WHERE scope = ?", (scope,), fetch=True)]
    return [tuple(r) for r in _run("SELECT name, body FROM mika_files WHERE scope = ? AND name = ?", (scope, name), fetch=True)]


def rows() -> list[tuple[str, str, str, str]]:
    return [tuple(r) for r in _run("SELECT scope, name, body, updated FROM mika_files", fetch=True)]


def scope_of(path: Path, data_dir: Path) -> tuple[str, str] | None:
    """Where a file under data/ lives in the table: its own student's scope, or the shared one."""
    try:
        rel = path.resolve().relative_to(data_dir.resolve())
    except ValueError:
        return None
    if len(rel.parts) == 3 and rel.parts[0] == "users":
        return f"user:{rel.parts[1]}", rel.parts[2]
    return "shared", rel.as_posix()


def _merge(old, new):
    """Two versions of one shared index or counter file: both sides kept. Lists are joined, counts are added, and for
    anything else the old value stands (it is what students already rely on: account codes, for instance)."""
    if isinstance(old, dict) and isinstance(new, dict):
        return {k: _merge(old[k], new[k]) if k in old and k in new else old.get(k, new.get(k)) for k in {**new, **old}}
    if isinstance(old, list) and isinstance(new, list):
        return old + [v for v in new if v not in old]
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool):
        return old + new
    return old


def copy_from(old_url: str) -> str:
    """Moving to another database: bring every row the old one holds into this one. Done once (a marker row says so);
    the old database is only read, never changed. Where both hold the same file:
      - a student's own file: the old one wins (anything written here since the move started from a blank state);
      - public data read from the web (raw/...): the newer one wins;
      - a shared index or counter (accounts, codes, usage, visits, reports, the service log): the two are merged."""
    if not enabled() or not old_url or old_url.strip() == URL:
        return ""
    if any(sc == "shared" and n == "_moved.json" for sc, n, _, _ in rows()):
        return ""                                  # already copied once: the variable can be removed whenever
    try:
        if old_url.startswith("postgres"):
            import psycopg
            with psycopg.connect(old_url) as old:
                found = old.execute("SELECT scope, name, body, updated FROM mika_files").fetchall()
        else:
            old = sqlite3.connect(re.sub(r"^sqlite:///", "", old_url))
            found = old.execute("SELECT scope, name, body, updated FROM mika_files").fetchall()
            old.close()
    except Exception as e:
        # the reason, without the address (it holds a password)
        why = re.sub(r"\S+://\S+", "[address]", str(e)).replace("\n", " ")[:160]
        return f"could not read the old database: {type(e).__name__}: {why}"
    import json
    have = {(sc, n): (b, u) for sc, n, b, u in rows()}
    put = lambda sc, n, b, u: _run("INSERT INTO mika_files (scope, name, body, updated) VALUES (?, ?, ?, ?) "
                                   "ON CONFLICT (scope, name) DO UPDATE SET body = excluded.body, updated = excluded.updated", (sc, n, b, u))
    added = kept_old = merged = 0
    for scope, name, body, updated in found:
        mine = have.get((scope, name))
        if mine is None:
            put(scope, name, body, updated); added += 1
        elif scope == "shared" and name.startswith("raw/"):
            if mine[1] < updated:
                put(scope, name, body, updated)
        elif scope == "shared":
            try:
                both = _merge(json.loads(body), json.loads(mine[0]))
                put(scope, name, json.dumps(both, ensure_ascii=False), max(updated, mine[1])); merged += 1
            except ValueError:
                put(scope, name, body, updated); kept_old += 1
        else:
            put(scope, name, body, updated); kept_old += 1
    keep("shared", "_moved.json", json.dumps({"added": added, "old_kept": kept_old, "merged": merged,
                                              "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    return (f"copied the {len(found)} rows of the old database: {added} new here, {kept_old} students' files taken from the old one, "
            f"{merged} shared files merged (done once; MIKA_COPY_FROM can now be removed)")


def restore(data_dir: Path) -> dict:
    """Write every stored file back under data/. Called once, before the first request is served."""
    out = {"users": set(), "shared": []}
    for scope, name, body, _ in rows():
        if ".." in name or name.startswith("/"):
            continue
        if scope.startswith("user:"):
            uid = scope[5:]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", uid) or "/" in name:
                continue
            target = data_dir / "users" / uid / name
            out["users"].add(uid)
        elif scope == "shared":
            target = data_dir / name
            out["shared"].append(name)
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return {"users": len(out["users"]), "shared": out["shared"]}
