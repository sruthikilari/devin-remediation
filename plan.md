# Plan

A GitHub label starts a Devin session that fixes the issue and opens a PR. The service only starts the session and follows it. Devin does the work.

## End-to-end flow

```
GitHub issue gets label `devin-remediate`
        |  webhook (issues / labeled)
        v
POST /webhook ── gate: right event, label, repo, issue open, no run Devin is still working on
        |
        v
Devin API: create session (prompt, repos, ACU cap, structured-output schema)
        |
        v
SQLite: run recorded with the session id and URL
        |
        v
Poller (every 30s): read session -> store status, PR, structured result
        |
        v
stopped: Devin hands off (waiting_for_user / finished)  |  fails (error, usage limit)  |  age limit (timed_out)
```

Devin does the rest by itself: it reads the issue, changes the code on the fork, runs the checks, opens a PR against the fork's default branch, comments once on the issue, and returns a structured result (`outcome`, `summary`, `files_changed`, `commands_run`, `not_run`, `pr_url`, ...). A human reviews and merges.

## Architecture

| Piece | Role |
|---|---|
| `main.py` | FastAPI app: webhook, dashboard, JSON endpoints; settings from environment variables |
| `devin.py` | Two Devin v3 calls: create a session and read one |
| `prompt.py` | The session prompt and the structured-output schema (the contract with Devin) |
| `status.py` | Devin's status vocabulary: when a session has stopped and which statuses are failures |
| `poller.py` | Background thread that follows each running session |
| `db.py` | SQLite: one table of runs and one of state changes |
| `metrics.py`, `dashboard.py` | Numbers and the server-rendered page |

Runs in one container (`docker compose up`). A public URL (ngrok) receives the GitHub webhook.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /webhook` | GitHub events. Only "trigger label added" starts a run |
| `GET /dashboard` | Success rate, time to PR, running and failed counts, runs table |
| `GET /metrics` | The same numbers as JSON |
| `GET /remediations` | All runs |
| `GET /remediations/{id}/events` | State changes for one run |
| `GET /healthz` | Liveness |

## Decisions

- **Devin does the work, the service stays thin.** Devin opens the PR and comments on the issue through the prompt. The service makes no GitHub calls.
- **Structured output is the contract.** Devin must return a result in a fixed schema. What it concluded (`fixed`, `not_actionable`, `blocked`) is recorded as the run's `outcome`, not as extra states.
- **Devin's own statuses.** The run status is Devin's `status_detail` (`working`, `waiting_for_user`, `finished`, ...), stored as reported. `status` stays `running` through handoff, so the detail is the signal. The service adds only the outcome from the structured result, and one status of its own, `timed_out`.
- **SQLite for state and history.** The poller reads its work from the database, so a restart resumes. The Devin API keeps sessions, so nothing is lost.
- **Guards kept small.** One running run per issue, a usage cap per session (`MAX_ACU_LIMIT`), a one-hour age limit, and the fork as the only target so a PR never goes to upstream.
- **Small, low-risk issues.** The three target issues are each a change of a few lines, so a human can review each PR quickly.
- **No claim of savings.** There is no baseline and Devin reports 0.0 ACU usage, so the dashboard shows outcomes and timing only.
