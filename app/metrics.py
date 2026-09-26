"""Numbers for the dashboard and ``/metrics``: rows in, dict out.

The success rate is Devin's own report, defined narrowly: of the runs that finished, the
share where Devin reported ``fixed``. Runs where Devin found nothing to change
(``not_actionable``, for example an issue that already has an open PR) are neutral: finished,
but left out of the rate. There is no "hours saved" and no cost figure: with a handful of
runs and no human baseline they would be misleading.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from app.status import FAILED



def _stats(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": min(values), "median": median(values), "max": max(values)}


def _per_day(rows: List[Dict[str, Any]], now: float, days: int = 7) -> List[Dict[str, Any]]:
    """Runs started and PRs opened per calendar day for the last ``days`` days, oldest first.

    Days are in the server's timezone (set ``TZ``), the same way a dashboard viewer's own
    clock would bucket them.
    """
    today = datetime.fromtimestamp(now).date()
    counts = {today - timedelta(days=i): {"day": (today - timedelta(days=i)).strftime("%b %d"), "started": 0, "prs": 0}
              for i in range(days - 1, -1, -1)}
    for r in rows:
        for key, ts in (("started", r.get("created_at")), ("prs", r.get("pr_opened_at"))):
            day = datetime.fromtimestamp(ts).date() if ts else None
            if day in counts:
                counts[day][key] += 1
    return list(counts.values())


def compute(rows: Iterable[Dict[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
    rows = list(rows)
    stopped_rows = [r for r in rows if r.get("completed_at")]      # Devin handed off or failed
    failed = [r for r in stopped_rows if r["devin_status"] in FAILED]
    handed_off = [r for r in stopped_rows if r["devin_status"] not in FAILED]
    fixed = sum(1 for r in handed_off if r.get("outcome") == "fixed")
    no_action = sum(1 for r in handed_off if r.get("outcome") == "not_actionable")
    other = len(handed_off) - fixed - no_action  # for example a blocked run
    rated = fixed + other + len(failed)
    acus = [r.get("acus_consumed") or 0.0 for r in rows]
    return {
        "total": len(rows),
        "active": len(rows) - len(stopped_rows),
        "finished": len(stopped_rows),
        "fixed": fixed, "no_action": no_action, "other": other, "failed": len(failed),
        "rated": rated, "rate": (fixed / rated) if rated else None,
        "seconds_to_pr": _stats([r["pr_opened_at"] - r["created_at"] for r in rows if r.get("pr_opened_at")]),
        "seconds_to_handoff": _stats([r["completed_at"] - r["created_at"] for r in handed_off]),
        "per_day": _per_day(rows, time.time() if now is None else now),
        "acus": {"total": sum(acus), "runs_reporting_nonzero": sum(1 for a in acus if a)},
    }
