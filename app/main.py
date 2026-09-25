"""FastAPI service: turns a GitHub label event into a Devin session and shows the results.

Run with:  uvicorn app.main:create_app --factory

Settings are environment variables (Docker Compose passes them from ``.env``):
DEVIN_API_KEY, DEVIN_ORG_ID and TARGET_REPO are required; MAX_ACU_LIMIT, MAX_RUN_MINUTES,
POLL_INTERVAL_SECONDS and DATABASE_PATH are optional.

Webhook payloads are NOT authenticated (no signature check) and the labeler is not
authorized (no permission check): anyone who can reach the endpoint can submit an event.
Keep the URL private and unguessable. The automation makes no GitHub API calls; Devin
opens the PR and comments on the issue itself.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.dashboard import render as render_dashboard
from app.db import Database
from app.devin import DevinClient
from app.metrics import compute as compute_metrics
from app.poller import Poller
from app.prompt import OUTPUT_SCHEMA, build_prompt

logger = logging.getLogger("app.webhook")

TRIGGER_LABEL = "devin-remediate"
Result = Tuple[int, Dict[str, Any]]


def ignore_reason(event_type: str, payload: Dict[str, Any], repo: str) -> Optional[str]:
    """Why a webhook is not a "trigger label added" event on the target repo, or None if it is."""
    label = (payload.get("label") or {}).get("name") or ""
    issue = payload.get("issue") or {}
    if event_type != "issues" or payload.get("action") != "labeled":
        return f"event '{event_type}' action '{payload.get('action')}' is not 'issues' 'labeled'"
    if label.casefold() != TRIGGER_LABEL:
        return f"label '{label}' is not the trigger label"
    if ((payload.get("repository") or {}).get("full_name") or "").casefold() != repo.casefold():
        return "repository is not the target repository"
    if not isinstance(issue.get("number"), int) or issue.get("state") == "closed":
        return "issue is missing or closed"
    return None


def create_app(devin: Optional[DevinClient] = None, db: Optional[Database] = None, repo: Optional[str] = None,
               start_poller: bool = True) -> FastAPI:
    env = os.environ
    missing = [n for n in ("DEVIN_API_KEY", "DEVIN_ORG_ID", "TARGET_REPO") if not env.get(n, "").strip()]
    if missing and not (devin and db and repo):
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    repo = repo or env["TARGET_REPO"]
    max_acu, max_run_minutes = int(env.get("MAX_ACU_LIMIT", 15)), int(env.get("MAX_RUN_MINUTES", 60))
    devin = devin or DevinClient(env["DEVIN_API_KEY"], env["DEVIN_ORG_ID"])
    db = db or Database(env.get("DATABASE_PATH", "./data/remediations.db"))
    dispatch_lock = threading.Lock()
    poller = Poller(db, devin, int(env.get("POLL_INTERVAL_SECONDS", 30)), max_run_minutes)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_poller:
            poller.start()
        yield
        poller.stop()
        devin.close()

    app = FastAPI(title="Devin remediation automation", lifespan=lifespan)

    def handle(event_type: str, payload: Dict[str, Any]) -> Result:
        reason = ignore_reason(event_type, payload, repo)
        if reason:
            logger.info("ignored: %s", reason)
            return 200, {"status": "ignored", "reason": reason}
        issue = payload["issue"]
        number, title, url = issue["number"], issue.get("title") or "", issue.get("html_url") or ""
        run = dict(repo=repo, issue_number=number, issue_title=title, issue_url=url)

        # One dispatch at a time: the run is recorded only after its session exists, so without the
        # lock two events for one issue could both pass the check. Enough for a single process.
        with dispatch_lock:
            if db.has_active(repo, number):
                logger.info("ignored issue #%s: already in progress", number)
                return 200, {"status": "ignored", "reason": "a run for this issue is already in progress"}
            try:
                session = devin.create_session(
                    build_prompt(repo=repo, issue_url=url, issue_body=issue.get("body") or ""),
                    title=f"Remediate #{number}: {' '.join(title.split())}"[:100],
                    repos=[repo],
                    max_acu_limit=max_acu,
                    structured_output_schema=OUTPUT_SCHEMA,
                    structured_output_required=True,
                )
                session_id = session["session_id"]
            except Exception as exc:  # any failure to start a session fails the run; re-label to retry
                logger.error("dispatch failed for issue #%s: %s", number, exc)
                db.create(**run, devin_status="error", stopped=True, reason=f"could not start a Devin session: {exc}")
                return 502, {"status": "error", "reason": "could not start Devin session"}
            rid = db.create(**run, session_id=session_id, session_url=session.get("url"))
            logger.info("dispatched issue #%s session=%s run=%s", number, session_id, rid)
            return 202, {"status": "dispatched", "run": rid, "session_id": session_id, "session_url": session.get("url")}

    @app.get("/healthz")
    async def healthz() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        rows = await run_in_threadpool(db.list_all, 10000)
        return HTMLResponse(render_dashboard(compute_metrics(rows), rows[:200], repo=repo, now=db.now()))

    @app.get("/metrics")
    async def metrics() -> Dict[str, Any]:
        return compute_metrics(await run_in_threadpool(db.list_all, 10000))

    @app.get("/remediations")
    async def remediations() -> Dict[str, Any]:
        rows = await run_in_threadpool(db.list_all)
        return {"count": len(rows), "remediations": [
            {k: r.get(k) for k in ("id", "issue_number", "attempt", "devin_status", "reason", "outcome", "session_url",
                                   "pr_url", "pr_state", "acus_consumed", "created_at", "completed_at")}
            for r in rows]}

    @app.get("/remediations/{rid}/events")
    async def remediation_events(rid: int) -> Dict[str, Any]:
        rem = await run_in_threadpool(db.get, rid)
        if rem is None:
            raise HTTPException(status_code=404, detail="no such run")
        return {"remediation_id": rid, "devin_status": rem["devin_status"], "events": await run_in_threadpool(db.events, rid)}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        event_type = request.headers.get("x-github-event", "")
        if event_type == "ping":
            return JSONResponse({"status": "pong"})
        try:
            payload = json.loads(await request.body())
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        status_code, content = await run_in_threadpool(handle, event_type, payload)
        return JSONResponse(content, status_code=status_code)

    return app
