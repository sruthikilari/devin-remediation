"""Background loop that follows each running Devin session to a conclusion.

Per pass, for every run Devin has not yet stopped: read the session from Devin and store its
status, PR and structured result. A run stops when Devin hands off or fails, or at the age
limit. Runs come from SQLite, so a restart just resumes. The only writes are to the database; nothing is written to Devin
or GitHub.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

import httpx

from app.db import Database
from app.devin import DevinClient
from app.status import status_of, stopped

logger = logging.getLogger("app.poller")


class Poller:
    def __init__(self, db: Database, devin: DevinClient, interval: int, max_run_minutes: int) -> None:
        self.db, self.devin, self.interval, self.max_run_minutes = db, devin, interval, max_run_minutes
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let the loop die
                logger.exception("poller pass failed")
            self._stop.wait(self.interval)

    def tick(self) -> None:
        for rem in self.db.active():
            self._poll(rem)

    def _poll(self, rem: Dict[str, Any]) -> None:
        status, reason, fields, done = rem["devin_status"], None, {}, False
        try:
            session = self.devin.get_session(rem["session_id"])
        except Exception as exc:  # a failed poll is retried on the next pass
            logger.warning("poll failed for run %s: %s", rem["id"], exc)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                status, reason, done = "error", "Devin session not found", True
        else:
            status, done = status_of(session), stopped(session)
            now = self.db.now()
            fields = {"acus_consumed": session.get("acus_consumed") or 0.0, "last_polled_at": now}
            prs = session.get("pull_requests") or []
            if prs:
                fields.update(pr_url=prs[0]["pr_url"], pr_state=prs[0].get("pr_state"))
                if not rem.get("pr_url"):
                    fields["pr_opened_at"] = now
            output = session.get("structured_output")
            if output and done:  # Devin drafts its output while working; only the snapshot at handoff is the result
                fields.update(structured_output=output, outcome=output.get("outcome"))
        # Backstop: a run still going after the age limit stops, even if its session cannot be read.
        # A session that stopped in time is handled above, so finishing always wins.
        if not done and self.db.now() - rem["created_at"] > self.max_run_minutes * 60:
            status, reason, done = "timed_out", f"timed out after {self.max_run_minutes} minutes", True
        if done:
            fields["completed_at"] = self.db.now()
        if done or fields or status != rem["devin_status"]:
            self.db.update(rem["id"], status, reason, **fields)
            if status != rem["devin_status"]:
                logger.info("run %s: %s -> %s%s", rem["id"], rem["devin_status"], status, f" ({reason})" if reason else "")
