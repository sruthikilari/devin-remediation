"""Devin's own session status, and the two questions the service asks of it.

The run's status is what Devin reports: ``status_detail`` (``working``, ``waiting_for_user``,
``finished``, ...) or, when there is no detail, ``status``. From real runs: ``status`` stays
``running`` while a session is active, including at handoff, so the detail is the signal. A
finished session left idle later shows ``suspended`` / ``inactivity``.

``stopped`` says Devin has stopped working: it handed off to a person or it failed.
``FAILED`` lists the statuses that mean the session did not do its job. ``timed_out`` is the
one status the service adds, for a run still going after the age limit.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

HANDOFF = {"waiting_for_user", "waiting_for_approval", "finished"}
FAILED = {
    "error", "timed_out", "usage_limit_exceeded", "out_of_credits", "out_of_quota", "no_quota_allocation",
    "payment_declined", "org_usage_limit_exceeded", "user_usage_limit_exceeded",
    "total_session_limit_exceeded", "contract_expired",
}


def status_of(session: Dict[str, Any]) -> Optional[str]:
    return session.get("status_detail") or session.get("status")


def stopped(session: Dict[str, Any]) -> bool:
    return status_of(session) in HANDOFF | FAILED or session.get("status") in ("exit", "error", "suspended")
