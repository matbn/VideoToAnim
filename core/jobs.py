"""Registro persistente de jobs em SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    video_name TEXT,
    video_bytes INTEGER,
    backend TEXT,
    params TEXT,
    status TEXT,
    created_at REAL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    log TEXT,
    artifacts TEXT,
    metrics TEXT,
    mesh_report TEXT
);
"""

_MIGRATIONS = [("mesh_report", "ALTER TABLE jobs ADD COLUMN mesh_report TEXT")]


class JobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            existing = {row[1] for row in c.execute("PRAGMA table_info(jobs)").fetchall()}
            for col, ddl in _MIGRATIONS:
                if col not in existing:
                    c.execute(ddl)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, video_name: str, video_bytes: int, backend: str, params: dict | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, video_name, video_bytes, backend, params, status, created_at, log, artifacts, metrics)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, video_name, int(video_bytes), backend,
                    json.dumps(params or {}, ensure_ascii=False),
                    "queued", time.time(), "", json.dumps({}, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                ),
            )
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("params", "artifacts", "metrics", "mesh_report") and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            cols.append(f"{k} = ?")
            vals.append(v)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", (*vals, job_id))

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT log FROM jobs WHERE id = ?", (job_id,)).fetchone()
            log = (row["log"] if row else "") or ""
            log += f"[{time.strftime('%H:%M:%S')}] {message}\n"
            c.execute("UPDATE jobs SET log = ? WHERE id = ?", (log, job_id))

    def get(self, job_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def set_artifact(self, job_id: str, name: str, path: str, size: int | None = None) -> None:
        job = self.get(job_id) or {}
        artifacts = job.get("artifacts") or {}
        artifacts[name] = {"path": path, "size": size}
        self.update(job_id, artifacts=artifacts)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in ("params", "artifacts", "metrics", "mesh_report"):
            try:
                d[k] = json.loads(d.get(k) or "null") if k == "mesh_report" else json.loads(d.get(k) or "{}")
            except (TypeError, json.JSONDecodeError):
                d[k] = None if k == "mesh_report" else {}
        return d
