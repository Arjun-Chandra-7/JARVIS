"""One SQLite file holding everything Jarvis remembers, searchable two ways at once.

The vault index this replaces kept every chunk and its 768-float vector in a single JSON blob:
loading it meant parsing the whole file, and searching it meant rebuilding a normalised numpy
matrix from scratch on every query. Keyword search was a separate `rg` pass whose results were
stapled underneath the semantic ones, so the two rankings never actually informed each other —
a note that both matched weakly and matched lexically ranked below a note that matched only one.

Here both live in one store:

    chunks      the text, its source kind, and where it came from
    chunks_fts  an FTS5 mirror -> BM25 keyword ranking, no external binary
    vecs        float32 embeddings as BLOBs -> cosine ranking

and `search()` fuses the two rankings with Reciprocal Rank Fusion, so agreement compounds and
either signal alone is still enough to surface something. FTS5 ships inside Python's own sqlite3,
so this adds no dependency beyond numpy, which is already required.

Three kinds of memory share the schema:

    note      a chunk of a Markdown file in the Obsidian vault (incremental, keyed on mtime)
    episode   something that was actually said, by whom and when
    fact      a durable distilled statement ("Arjun's sister is called Meera")

Everything degrades. With no embedder the store is a competent BM25 index; the vector half simply
contributes nothing to the fusion.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

EmbedFn = Callable[[str], Optional[Sequence[float]]]

KINDS = ("note", "episode", "fact")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL,
    ref     TEXT NOT NULL,          -- vault-relative path, or a speaker for episodes
    ord     INTEGER NOT NULL DEFAULT 0,
    mtime   REAL    NOT NULL DEFAULT 0,   -- source mtime, for incremental note sync
    ts      REAL    NOT NULL DEFAULT 0,   -- when this memory happened
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_ref  ON chunks(kind, ref);
CREATE INDEX IF NOT EXISTS chunks_time ON chunks(kind, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS vecs (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    dim      INTEGER NOT NULL,
    vec      BLOB    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Keep the FTS mirror in step with the base table without remembering to do it by hand.
_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

_RRF_K = 60          # standard RRF damping; large enough that rank 1 vs 2 isn't a landslide
_FTS_POOL = 120      # candidates pulled from each ranking before fusion
_VEC_POOL = 120


def chunk_markdown(text: str, max_chars: int = 1200) -> list[str]:
    """Split on blank lines, packing paragraphs up to `max_chars`; hard-split anything longer."""
    chunks: list[str] = []
    current = ""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        elif len(para) <= max_chars:
            if current:
                chunks.append(current)
            current = para
        else:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(para[i : i + max_chars] for i in range(0, len(para), max_chars))
    if current:
        chunks.append(current)
    return chunks


def _fts_query(text: str) -> str:
    """Turn free text into an FTS5 OR-query, quoting each term so punctuation can't inject syntax."""
    terms = [t for t in re.findall(r"\w+", (text or "").lower()) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:24])


def _to_blob(vec: Sequence[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


class MemoryStore:
    """A hybrid keyword+vector memory. Safe to open from several threads; one file on disk."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self.db.executescript(_TRIGGERS)
        self.db.commit()

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass

    # -- writing -----------------------------------------------------------
    def _insert(self, kind: str, ref: str, text: str, *, ord_: int = 0, mtime: float = 0.0,
                ts: float = 0.0, vec: Optional[Sequence[float]] = None) -> int:
        cur = self.db.execute(
            "INSERT INTO chunks(kind, ref, ord, mtime, ts, text) VALUES (?,?,?,?,?,?)",
            (kind, ref, ord_, mtime, ts or time.time(), text),
        )
        cid = int(cur.lastrowid)
        if vec is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO vecs(chunk_id, dim, vec) VALUES (?,?,?)",
                (cid, len(vec), _to_blob(vec)),
            )
        return cid

    def add_episode(self, who: str, text: str, *, ts: Optional[float] = None,
                    embed: Optional[EmbedFn] = None) -> int:
        """Record something that was said. This is what makes 'what did we discuss on Tuesday' work."""
        text = (text or "").strip()
        if not text:
            return 0
        vec = embed(text) if embed else None
        cid = self._insert("episode", who or "?", text, ts=ts or time.time(), vec=vec)
        self.db.commit()
        return cid

    def add_fact(self, text: str, *, ref: str = "profile", embed: Optional[EmbedFn] = None) -> int:
        """Store a durable statement, replacing an identical one rather than accumulating copies."""
        text = (text or "").strip()
        if not text:
            return 0
        self.db.execute("DELETE FROM chunks WHERE kind='fact' AND text=?", (text,))
        cid = self._insert("fact", ref, text, vec=embed(text) if embed else None)
        self.db.commit()
        return cid

    def forget(self, kind: str, ref: str) -> int:
        cur = self.db.execute("DELETE FROM chunks WHERE kind=? AND ref=?", (kind, ref))
        self.db.commit()
        return cur.rowcount

    def prune_episodes(self, keep_days: float = 400.0) -> int:
        """Drop conversation turns older than `keep_days`. Notes and facts are never auto-pruned."""
        cutoff = time.time() - keep_days * 86400
        cur = self.db.execute("DELETE FROM chunks WHERE kind='episode' AND ts < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    # -- vault sync --------------------------------------------------------
    def _note_mtimes(self) -> dict[str, float]:
        rows = self.db.execute("SELECT ref, MAX(mtime) m FROM chunks WHERE kind='note' GROUP BY ref")
        return {r["ref"]: r["m"] for r in rows}

    def sync_vault(self, vault: Path, embed: Optional[EmbedFn] = None,
                   progress: Optional[Callable[[str, int], None]] = None) -> dict:
        """Bring the note index in line with the vault. Unchanged files are not re-embedded.

        Private material (`Jarvis/private/`) is excluded here as it is everywhere else — imported
        chat history must not leak into ordinary recall.
        """
        vault = Path(vault)
        known = self._note_mtimes()
        seen: set[str] = set()
        stats = {"indexed": 0, "unchanged": 0, "removed": 0, "chunks": 0, "embedded": 0}

        for file in sorted(vault.rglob("*.md")):
            parts = set(file.parts)
            if {".jarvis", ".git", "private"} & parts:
                continue
            rel = str(file.relative_to(vault))
            seen.add(rel)
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue
            if rel in known and abs(known[rel] - mtime) < 1e-6:
                stats["unchanged"] += 1
                continue
            try:
                text = file.read_text(errors="ignore")
            except OSError:
                continue
            self.db.execute("DELETE FROM chunks WHERE kind='note' AND ref=?", (rel,))
            for i, chunk in enumerate(chunk_markdown(text)):
                vec = embed(chunk) if embed else None
                if vec is not None:
                    stats["embedded"] += 1
                self._insert("note", rel, chunk, ord_=i, mtime=mtime, ts=mtime, vec=vec)
                stats["chunks"] += 1
                if progress:
                    progress(rel, stats["chunks"])
            stats["indexed"] += 1
            self.db.commit()

        for gone in set(known) - seen:
            self.db.execute("DELETE FROM chunks WHERE kind='note' AND ref=?", (gone,))
            stats["removed"] += 1
        self.db.commit()
        return stats

    # -- reading -----------------------------------------------------------
    def _keyword_rank(self, query: str, kinds: Sequence[str], pool: int) -> list[int]:
        match = _fts_query(query)
        if not match:
            return []
        sql = (
            "SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            "WHERE chunks_fts MATCH ? AND c.kind IN (%s) ORDER BY bm25(chunks_fts) LIMIT ?"
            % ",".join("?" * len(kinds))
        )
        try:
            rows = self.db.execute(sql, (match, *kinds, pool)).fetchall()
        except sqlite3.OperationalError:  # malformed MATCH despite quoting — treat as no hits
            return []
        return [int(r["id"]) for r in rows]

    def _vector_rank(self, query_vec: Sequence[float], kinds: Sequence[str], pool: int) -> list[int]:
        sql = (
            "SELECT v.chunk_id, v.dim, v.vec FROM vecs v JOIN chunks c ON c.id = v.chunk_id "
            "WHERE c.kind IN (%s)" % ",".join("?" * len(kinds))
        )
        rows = self.db.execute(sql, tuple(kinds)).fetchall()
        if not rows:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        dim = q.shape[0]
        ids: list[int] = []
        mats: list[np.ndarray] = []
        for r in rows:
            if r["dim"] != dim:      # a re-embed with a different model left stale rows behind
                continue
            ids.append(int(r["chunk_id"]))
            mats.append(np.frombuffer(r["vec"], dtype=np.float32))
        if not ids:
            return []
        matrix = np.vstack(mats)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8
        sims = matrix @ (q / (np.linalg.norm(q) + 1e-8))
        order = np.argsort(-sims)[:pool]
        return [ids[i] for i in order]

    def search(self, query: str, k: int = 6, *, kinds: Optional[Sequence[str]] = None,
               embed: Optional[EmbedFn] = None) -> list[dict]:
        """Hybrid search. Returns [{id, kind, ref, text, ts, score, matched}] best first.

        `matched` says which signals fired ("keyword", "semantic", or both) — useful when the
        caller wants to explain itself, and when debugging why something did or didn't surface.
        """
        query = (query or "").strip()
        if not query:
            return []
        kinds = tuple(kinds or KINDS)

        keyword = self._keyword_rank(query, kinds, _FTS_POOL)
        semantic: list[int] = []
        if embed:
            qv = embed(query)
            if qv is not None:
                semantic = self._vector_rank(qv, kinds, _VEC_POOL)

        fused: dict[int, float] = {}
        matched: dict[int, set[str]] = {}
        for ranking, label in ((keyword, "keyword"), (semantic, "semantic")):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
                matched.setdefault(cid, set()).add(label)

        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
        if not top:
            return []
        by_id = {
            int(r["id"]): r
            for r in self.db.execute(
                "SELECT id, kind, ref, ts, text FROM chunks WHERE id IN (%s)"
                % ",".join("?" * len(top)),
                tuple(cid for cid, _ in top),
            )
        }
        out = []
        for cid, score in top:
            row = by_id.get(cid)
            if row is None:
                continue
            out.append({
                "id": cid, "kind": row["kind"], "ref": row["ref"], "ts": row["ts"],
                "text": row["text"], "score": round(score, 5),
                "matched": sorted(matched.get(cid, ())),
            })
        return out

    def recent_episodes(self, limit: int = 20, since: Optional[float] = None) -> list[dict]:
        sql = "SELECT ref, text, ts FROM chunks WHERE kind='episode'"
        args: list = []
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        return [{"who": r["ref"], "text": r["text"], "ts": r["ts"]} for r in reversed(rows)]

    def stats(self) -> dict:
        out = {"path": str(self.path)}
        for kind in KINDS:
            out[kind] = int(
                self.db.execute("SELECT COUNT(*) c FROM chunks WHERE kind=?", (kind,)).fetchone()["c"]
            )
        out["vectors"] = int(self.db.execute("SELECT COUNT(*) c FROM vecs").fetchone()["c"])
        try:
            out["size_kb"] = round(self.path.stat().st_size / 1024, 1)
        except OSError:
            out["size_kb"] = 0.0
        return out


_STORE: Optional[MemoryStore] = None


def default_path(vault: Optional[Path] = None) -> Path:
    if vault is not None:
        return Path(vault) / ".jarvis" / "memory.db"
    root = Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()
    return root / "memory.db"


def get_store(vault: Optional[Path] = None) -> MemoryStore:
    """Process-wide store. Keyed on nothing — Jarvis has exactly one memory."""
    global _STORE
    if _STORE is None:
        _STORE = MemoryStore(default_path(vault))
    return _STORE
