"""SQLite state: one row per run, plus an audit trail of Devin status changes.

Stdlib ``sqlite3``, one short-lived connection per call (WAL mode), so the webhook
handlers and the poller thread can share the file.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remediations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_title TEXT,
    issue_url TEXT,
    attempt INTEGER NOT NULL,
    devin_status TEXT NOT NULL,
    reason TEXT,
    session_id TEXT,
    session_url TEXT,
    pr_url TEXT,
    pr_state TEXT,
    pr_opened_at REAL,
    acus_consumed REAL,
    outcome TEXT,
    structured_output TEXT,
    created_at REAL NOT NULL,
    completed_at REAL,
    last_polled_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remediation_id INTEGER NOT NULL,
    at REAL NOT NULL,
    from_status TEXT,
    to_status TEXT,
    detail TEXT
);
"""


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    if data.get("structured_output"):
        data["structured_output"] = json.loads(data["structured_output"])
    return data


class Database:
    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.path, self.now = path, clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _query(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            return [_row(r) for r in conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    def create(self, *, repo: str, issue_number: int, issue_title: str, issue_url: str,
               devin_status: str = "new", reason: Optional[str] = None, stopped: bool = False,
               session_id: Optional[str] = None, session_url: Optional[str] = None) -> int:
        with self._conn() as conn:
            attempt = conn.execute("SELECT COUNT(*) + 1 FROM remediations WHERE repo = ? AND issue_number = ?",
                                   (repo, issue_number)).fetchone()[0]
            now = self.now()
            rid = conn.execute(
                "INSERT INTO remediations (repo, issue_number, issue_title, issue_url, attempt, devin_status, reason, "
                "session_id, session_url, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (repo, issue_number, issue_title, issue_url, attempt, devin_status, reason, session_id, session_url,
                 now, now if stopped else None)).lastrowid
            conn.execute("INSERT INTO events (remediation_id, at, to_status, detail) VALUES (?, ?, ?, ?)",
                         (rid, now, devin_status, reason))
            return int(rid)

    def update(self, rid: int, devin_status: Optional[str] = None, reason: Optional[str] = None, **fields: Any) -> None:
        """Store fields on a run. A change of Devin status is also logged as an event."""
        with self._conn() as conn:
            old = conn.execute("SELECT devin_status FROM remediations WHERE id = ?", (rid,)).fetchone()["devin_status"]
            if devin_status and devin_status != old:
                fields.update(devin_status=devin_status)
                conn.execute("INSERT INTO events (remediation_id, at, from_status, to_status, detail) VALUES (?, ?, ?, ?, ?)",
                             (rid, self.now(), old, devin_status, reason))
            if reason:
                fields["reason"] = reason
            if isinstance(fields.get("structured_output"), dict):
                fields["structured_output"] = json.dumps(fields["structured_output"])
            if fields:
                conn.execute(f"UPDATE remediations SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                             (*fields.values(), rid))

    def get(self, rid: int) -> Optional[Dict[str, Any]]:
        rows = self._query("SELECT * FROM remediations WHERE id = ?", (rid,))
        return rows[0] if rows else None

    def list_all(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM remediations ORDER BY id DESC LIMIT ?", (limit,))

    def active(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM remediations WHERE completed_at IS NULL ORDER BY id")

    def has_active(self, repo: str, issue_number: int) -> bool:
        return bool(self._query("SELECT 1 FROM remediations WHERE repo = ? AND issue_number = ? AND completed_at IS NULL",
                                (repo, issue_number)))

    def events(self, rid: int) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM events WHERE remediation_id = ? ORDER BY id", (rid,))
