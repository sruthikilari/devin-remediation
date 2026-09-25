"""One server-rendered HTML page. No JavaScript, no chart library.

The chart is an inline SVG donut. Everything that came from GitHub
or Devin (issue titles, reasons, URLs) is untrusted, so it is HTML-escaped, and
links are only rendered for http(s) URLs.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.status import FAILED

REFRESH_SECONDS = 15

_CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--accent:#2563eb;
--ok:#16a34a;--warn:#d97706;--bad:#dc2626;--info:#2563eb;--idle:#a8a29e;--track:#e7e5e4}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#f5f5f4;--muted:#a8a29e;--card:#1c1917;--line:#2b2825;
--accent:#60a5fa;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--info:#60a5fa;--idle:#78716c;--track:#2b2825}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}h1{font-size:20px;margin:0 0 2px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:26px 0 10px}
.sub{color:var(--muted);margin:0 0 18px;font-size:13px}a{color:var(--accent)}.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:28px;line-height:1.15}.tile span{color:var(--muted);font-size:12px}
.tile.hero b{font-size:36px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
.card h3{margin:0 0 10px;font-size:13px}
.donutbox{display:flex;align-items:center;gap:16px}.donut{width:132px;height:132px;flex:none}
.donut circle{fill:none;stroke-width:5}.donut .track{stroke:var(--track)}
.donut text{fill:var(--fg);text-anchor:middle}.donut .big{font-size:8px;font-weight:700}.donut .small{font-size:3px;fill:var(--muted)}
.legend{list-style:none;margin:0;padding:0;font-size:13px}.legend li{margin:3px 0;white-space:nowrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:7px;vertical-align:-1px}
.c-ok{stroke:var(--ok);background:var(--ok)}.c-info{stroke:var(--info);background:var(--info)}
.c-warn{stroke:var(--warn);background:var(--warn)}.c-bad{stroke:var(--bad);background:var(--bad)}
.c-idle{stroke:var(--idle);background:var(--idle)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid currentColor}
.ok{color:var(--ok)}.bad{color:var(--bad)}.busy{color:var(--info)}
.scroll{overflow-x:auto}.note{color:var(--muted);font-size:12px;margin-top:22px;padding-left:18px}.note li{margin:2px 0}
"""

Slice = Tuple[str, int, str]  # label, value, css class


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(round(seconds)))
    return f"{total // 60}m {total % 60:02d}s"


def fmt_time(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d %H:%M:%S") if ts else "-"


def pct(rate: Optional[float]) -> str:
    return "-" if rate is None else f"{round(rate * 100)}%"


def safe_link(url: Optional[str], label: str) -> str:
    """An anchor for http(s) URLs only; plain escaped text otherwise."""
    if url and url.startswith(("https://", "http://")):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'
    return escape(label)


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def donut(slices: Sequence[Slice], centre_big: str, centre_small: str) -> str:
    """A donut chart with a legend. Segments are SVG circles (circumference = 100)."""
    total = sum(v for _, v, _ in slices)
    segments: List[str] = ['<circle class="track" cx="21" cy="21" r="15.9155"/>']
    cumulative = 0.0
    for label, value, cls in slices:
        if not value:
            continue
        share = value / total * 100
        segments.append(
            f'<circle class="{cls}" cx="21" cy="21" r="15.9155" stroke-dasharray="{share:.2f} {100 - share:.2f}" '
            f'stroke-dashoffset="{25 - cumulative:.2f}"><title>{escape(label)}: {value}</title></circle>'
        )
        cumulative += share
    legend = "".join(
        f'<li><span class="dot {cls}"></span>{escape(label)} <b>{value}</b></li>' for label, value, cls in slices
    )
    return (
        '<div class="donutbox">'
        f'<svg class="donut" viewBox="0 0 42 42" role="img" aria-label="{escape(centre_small)}">{"".join(segments)}'
        f'<text class="big" x="21" y="22.5">{escape(centre_big)}</text>'
        f'<text class="small" x="21" y="27">{escape(centre_small)}</text></svg>'
        f'<ul class="legend">{legend}</ul></div>'
    )


# --------------------------------------------------------------------------- #
# Table
# --------------------------------------------------------------------------- #
def _row(r: Dict[str, Any]) -> str:
    out = r.get("structured_output") or {}
    ran = out.get("commands_run") or []
    validation = f"{sum(1 for c in ran if c.get('passed'))}/{len(ran)}" if ran else "-"
    to_pr = fmt_duration(r["pr_opened_at"] - r["created_at"]) if r.get("pr_opened_at") else "-"
    failed = r["devin_status"] in FAILED
    # Only a run Devin handed off has a time to handoff; failed rows also carry a stop time.
    to_done = fmt_duration(r["completed_at"] - r["created_at"]) if r.get("completed_at") and not failed else "-"
    tone = "bad" if failed else "ok" if r.get("completed_at") else "busy"
    pr = safe_link(r.get("pr_url"), "PR") if r.get("pr_url") else "-"
    issue = safe_link(r.get("issue_url"), f"#{r['issue_number']}")
    return (
        "<tr>"
        f"<td>{issue}<div class='muted'>{escape((r.get('issue_title') or '')[:60])}</div></td>"
        f"<td><span class='pill {tone}'>{escape(r['devin_status'])}</span>"
        f"<div class='muted'>{escape(r.get('reason') or r.get('outcome') or '')}</div></td>"
        f"<td>{safe_link(r.get('session_url'), 'session') if r.get('session_url') else '-'}</td>"
        f"<td>{pr}</td><td>{to_pr}</td><td>{to_done}</td><td>{validation}</td>"
        f"<td class='muted'>{fmt_time(r.get('created_at'))}</td>"
        f"<td><a href='/remediations/{r['id']}/events'>events</a></td>"
        "</tr>"
    )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def render(m: Dict[str, Any], rows: List[Dict[str, Any]], *, repo: str, now: float) -> str:
    acu_note = "" if m["acus"]["runs_reporting_nonzero"] else " ACUs are not shown: the API reported 0.0 for every run, because this plan meters usage as a daily and weekly quota rather than in ACUs (ACUs apply to Enterprise plans)."
    tiles = (
        f'<div class="tile hero"><b>{pct(m["rate"])}</b><span>Success rate: {m["fixed"]} of {m["rated"]} rated runs</span></div>'
        f'<div class="tile"><b>{fmt_duration(m["seconds_to_pr"]["median"])}</b><span>Median to PR (n={m["seconds_to_pr"]["n"]})</span></div>'
        f'<div class="tile"><b>{fmt_duration(m["seconds_to_handoff"]["median"])}</b><span>Median to handoff (n={m["seconds_to_handoff"]["n"]})</span></div>'
        f'<div class="tile"><b>{m["active"]}</b><span>Running now</span></div>'
        f'<div class="tile"><b>{m["failed"]}</b><span>Failed</span></div>'
    )
    outcomes = donut(
        [("Fixed", m["fixed"], "c-ok"), ("No change needed", m["no_action"], "c-idle"),
         ("Other (e.g. blocked)", m["other"], "c-warn"), ("Failed", m["failed"], "c-bad")],
        pct(m["rate"]), f"{m['rated']} rated")
    rows_html = "".join(_row(r) for r in rows) or '<tr><td colspan="9" class="muted">No runs yet. Add the trigger label to an issue.</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}"><title>Devin remediation</title><style>{_CSS}</style></head>
<body><main>
<h1>Devin remediation</h1>
<p class="sub">{escape(repo)} &middot; {m['total']} runs &middot; updated {fmt_time(now)}</p>
<div class="tiles">{tiles}</div>

<h2>Outcomes</h2>
<div class="card" style="max-width:420px">{outcomes}</div>

<h2>Runs</h2>
<div class="scroll"><table><thead><tr>
<th>Issue</th><th>Devin status</th><th>Session</th><th>PR</th><th>To PR</th><th>To handoff</th><th>Tests</th><th>Started</th><th></th>
</tr></thead><tbody>{rows_html}</tbody></table></div>

<ul class="note">
<li><b>Success rate</b> = runs where Devin reported <i>fixed</i>, out of finished runs, leaving out "no change needed" runs (for example an issue that already has an open PR) and runs still running. It is Devin's own report, not proof the fix is correct. Every PR needs human review.</li>
<li>Merges are not tracked yet. When Devin finishes, the PR is handed off to a person, and the service stops following the run, so a later merge or close does not appear here. Check the PR on GitHub for its current state.</li>
<li>No baseline exists for a human doing the same work, so no time or cost saving is claimed. Small sample: {m['total']} runs. Times are observed every 30 seconds.{escape(acu_note)}</li>
</ul>
</main></body></html>"""
