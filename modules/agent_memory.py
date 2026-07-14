# File: ~/.config/orkesai/modules/agent_memory.py
"""The ONE shared memory — a single SQLite brain used by both the GUI and the
terminal, so what one frontend learns the other knows.

Every memory is one row the user fully controls (list/add/edit/pin/delete —
GUI Settings → Memory, terminal /mem, HTTP /api/memories):

  scope       global | role:<agent id> | project:<path>
  kind        fact | preference | learning
  importance  pinned    — never auto-pruned, always recalled first
              normal    — recalled by relevance/recency
              ephemeral — auto-pruned when the store grows past its cap

Recall is retrieval, not inject-everything: pinned memories always ride along,
the rest are found with SQLite FTS5 (falls back to LIKE when FTS5 is missing)
ranked against the current message. Stdlib only.
"""
import json
import os
import re
import sqlite3
import threading
import time
import uuid

CFG_DIR = os.path.expanduser("~/.config/orkesai")
DB_FILE = os.path.join(CFG_DIR, ".memory.db")
_lock = threading.Lock()
_conn = None
_fts_ok = False

VALID_KINDS = ("fact", "preference", "learning")
VALID_IMPORTANCE = ("pinned", "normal", "ephemeral")
_EPHEMERAL_CAP = 300  # per scope; oldest unpinned ephemerals pruned past this


def _db() -> sqlite3.Connection:
    global _conn, _fts_ok
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        os.makedirs(CFG_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS memories(
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'global',
            kind TEXT NOT NULL DEFAULT 'fact',
            title TEXT DEFAULT '',
            body TEXT NOT NULL,
            importance TEXT NOT NULL DEFAULT 'normal',
            source TEXT DEFAULT '',
            created INTEGER, updated INTEGER,
            last_used INTEGER DEFAULT 0, uses INTEGER DEFAULT 0)""")
        try:
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                            USING fts5(id UNINDEXED, title, body)""")
            _fts_ok = True
        except sqlite3.OperationalError:
            _fts_ok = False  # python built without FTS5 → LIKE fallback
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        _conn = conn
    # one-time: absorb the terminal's legacy TPM facts into the brain
    try:
        done = conn.execute("SELECT value FROM meta WHERE key='tpm_migrated'").fetchone()
        if not done:
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('tpm_migrated', '1')")
            conn.commit()
            migrate_legacy_tpm()
    except Exception:
        pass
    return _conn


def _row(r) -> dict:
    keys = ("id", "scope", "kind", "title", "body", "importance", "source",
            "created", "updated", "last_used", "uses")
    return dict(zip(keys, r))


_SELECT = ("SELECT id, scope, kind, title, body, importance, source, "
           "created, updated, last_used, uses FROM memories")


def add_memory(body: str, scope: str = "global", kind: str = "fact",
               title: str = "", importance: str = "normal", source: str = ""):
    body = str(body or "").strip()
    if not body:
        return None, "memory body is required"
    scope = str(scope or "global").strip() or "global"
    kind = kind if kind in VALID_KINDS else "fact"
    importance = importance if importance in VALID_IMPORTANCE else "normal"
    conn = _db()
    with _lock:
        # the same thought in the same scope is one memory, not two
        dup = conn.execute("SELECT id FROM memories WHERE scope=? AND body=?",
                           (scope, body)).fetchone()
        if dup:
            return get_memory(dup[0]), ""
        mid = uuid.uuid4().hex[:12]
        now = int(time.time())
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,0,0)",
                     (mid, scope, kind, str(title or "")[:120], body[:4000],
                      importance, str(source or "")[:80], now, now))
        if _fts_ok:
            conn.execute("INSERT INTO memories_fts(id, title, body) VALUES (?,?,?)",
                         (mid, str(title or ""), body[:4000]))
        conn.commit()
    _prune(scope)
    return get_memory(mid), ""


def get_memory(mid: str):
    r = _db().execute(_SELECT + " WHERE id=?", (mid,)).fetchone()
    return _row(r) if r else None


def update_memory(mid: str, data: dict):
    m = get_memory(mid)
    if not m:
        return None, "memory not found"
    fields = {}
    if "body" in data and str(data["body"]).strip():
        fields["body"] = str(data["body"]).strip()[:4000]
    if "title" in data:
        fields["title"] = str(data["title"] or "")[:120]
    if "kind" in data and data["kind"] in VALID_KINDS:
        fields["kind"] = data["kind"]
    if "importance" in data and data["importance"] in VALID_IMPORTANCE:
        fields["importance"] = data["importance"]
    if "scope" in data and str(data["scope"]).strip():
        fields["scope"] = str(data["scope"]).strip()
    if not fields:
        return m, ""
    fields["updated"] = int(time.time())
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _db()
    with _lock:
        conn.execute(f"UPDATE memories SET {sets} WHERE id=?", (*fields.values(), mid))
        if _fts_ok and ("body" in fields or "title" in fields):
            conn.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
            m2 = get_memory(mid)
            conn.execute("INSERT INTO memories_fts(id, title, body) VALUES (?,?,?)",
                         (mid, m2["title"], m2["body"]))
        conn.commit()
    return get_memory(mid), ""


def delete_memory(mid: str):
    conn = _db()
    with _lock:
        conn.execute("DELETE FROM memories WHERE id=?", (mid,))
        if _fts_ok:
            conn.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
        conn.commit()
    return True, ""


def wipe_scope(scope: str):
    conn = _db()
    with _lock:
        ids = [r[0] for r in conn.execute("SELECT id FROM memories WHERE scope=?", (scope,))]
        conn.execute("DELETE FROM memories WHERE scope=?", (scope,))
        if _fts_ok and ids:
            conn.executemany("DELETE FROM memories_fts WHERE id=?", [(i,) for i in ids])
        conn.commit()
    return True, ""


def list_memories(scope: str = "", kind: str = "", q: str = "", limit: int = 200) -> list:
    conn = _db()
    where, args = [], []
    if scope:
        where.append("scope=?"); args.append(scope)
    if kind:
        where.append("kind=?"); args.append(kind)
    if q:
        where.append("(title LIKE ? OR body LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY CASE importance WHEN 'pinned' THEN 0 ELSE 1 END, updated DESC LIMIT ?"
    args.append(max(1, min(int(limit or 200), 500)))
    return [_row(r) for r in conn.execute(sql, args)]


def _fts_query(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]{3,}", text or "")[:12]
    return " OR ".join(words)


def recall(scopes: list, query_text: str = "", k: int = 8) -> list:
    """The memories a turn should see: every PINNED memory in scope, then the
    most relevant/recent normal+ephemeral ones up to k total."""
    conn = _db()
    scopes = list(dict.fromkeys(scopes or ["global"]))
    ph = ",".join("?" for _ in scopes)
    out = [_row(r) for r in conn.execute(
        _SELECT + f" WHERE scope IN ({ph}) AND importance='pinned' ORDER BY updated DESC LIMIT 20",
        scopes)]
    rest = max(0, k - len(out))
    if rest:
        found = []
        fq = _fts_query(query_text)
        if _fts_ok and fq:
            try:
                ids = [r[0] for r in conn.execute(
                    "SELECT id FROM memories_fts WHERE memories_fts MATCH ? LIMIT 40", (fq,))]
                if ids:
                    ph2 = ",".join("?" for _ in ids)
                    found = [_row(r) for r in conn.execute(
                        _SELECT + f" WHERE id IN ({ph2}) AND scope IN ({ph}) "
                        "AND importance!='pinned' ORDER BY updated DESC", (*ids, *scopes))]
            except sqlite3.OperationalError:
                found = []
        if not found:
            found = [_row(r) for r in conn.execute(
                _SELECT + f" WHERE scope IN ({ph}) AND importance!='pinned' "
                "ORDER BY updated DESC LIMIT ?", (*scopes, rest))]
        out += found[:rest]
    if out:
        now = int(time.time())
        with _lock:
            conn.executemany("UPDATE memories SET last_used=?, uses=uses+1 WHERE id=?",
                             [(now, m["id"]) for m in out])
            conn.commit()
    return out


def _prune(scope: str) -> None:
    """Ephemerals are allowed to age out; pinned never, normals never (yet)."""
    conn = _db()
    with _lock:
        n = conn.execute("SELECT COUNT(*) FROM memories WHERE scope=?", (scope,)).fetchone()[0]
        if n <= _EPHEMERAL_CAP:
            return
        doomed = [r[0] for r in conn.execute(
            "SELECT id FROM memories WHERE scope=? AND importance='ephemeral' "
            "ORDER BY last_used ASC, updated ASC LIMIT ?", (scope, n - _EPHEMERAL_CAP))]
        if doomed:
            conn.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in doomed])
            if _fts_ok:
                conn.executemany("DELETE FROM memories_fts WHERE id=?", [(i,) for i in doomed])
            conn.commit()


def stats() -> dict:
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_scope = dict(conn.execute("SELECT scope, COUNT(*) FROM memories GROUP BY scope"))
    return {"total": total, "by_scope": by_scope, "fts": _fts_ok, "file": DB_FILE}


def migrate_legacy_tpm(roots: list = None) -> int:
    """Pull facts out of the terminal's old per-workspace TPM stores
    (tpm_memories key/value tables) into the shared brain, one memory per fact
    as 'key: value', scoped per legacy workspace. Safe to re-run — duplicate
    bodies collapse. Runs automatically ONCE when the brain is first created."""
    roots = roots or [os.path.join(CFG_DIR, "projects", "database")]
    before = _db().execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if not name.endswith((".db", ".sqlite")):
                continue
            try:
                old = sqlite3.connect(f"file:{os.path.join(root, name)}?mode=ro", uri=True)
                try:
                    rows = old.execute("SELECT key, value FROM tpm_memories").fetchall()
                except sqlite3.OperationalError:
                    rows = []
                scope = f"project:{name.rsplit('.', 1)[0]}"
                for key, value in rows:
                    key, value = str(key or "").strip(), str(value or "").strip()
                    if not value:
                        continue
                    body = f"{key}: {value}" if key else value
                    add_memory(body, scope=scope, kind="fact", source="tpm-migration")
                old.close()
            except Exception:
                continue
    return _db().execute("SELECT COUNT(*) FROM memories").fetchone()[0] - before
