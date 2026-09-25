# Devin remediation automation

Label a GitHub issue `devin-remediate` and a Devin session investigates it, fixes it, validates the fix and opens a pull request. This service starts the session, follows it to a conclusion and shows what happened on a dashboard. A human reviews every PR; nothing is merged automatically.

It was built against [sruthikilari/superset](https://github.com/sruthikilari/superset), a fork of [apache/superset](https://github.com/apache/superset). Three real issues on that repo (a `md5` portability fix, default SMTP credentials, and a `js-yaml` dependency vulnerability) were filed for Devin to remediate through this automation. They are listed in [issues.md](issues.md).

**Contents:** [Why it matters](#why-this-matters) · [End to end flow](#end-to-end-flow) · [Architecture](#architecture) · [Running this solution](#running-this-solution) · [Devin configuration](#how-devin-is-configured) · [Status lifecycle](#status-lifecycle) · [Dashboard](#the-dashboard) · [API](#api) · [Why Devin](#why-devin-is-the-core-of-this-solution) · [Roadmap](#full-automation-roadmap) · [Expansion](#expansion-ideas) · [Layout](#project-layout)

## Why this matters

**The problem.** Small, well-understood findings (a dependency advisory, an insecure default, a portability flag) are the work that gets deferred, because each one still costs an engineer a context switch: set up an unfamiliar repo (this one needs Node 24 and npm 11 for the frontend), find every affected site, make the change, run the right checks and write it up. This service takes an issue from "filed" to "reviewable PR" without spending that engineer's time. A person still reviews and merges every PR.

**Why Devin and not a script.** A script can make the edit it was written for. Remediation needs judgment about the specific issue. Devin checks the issue's claims against the code, can find affected sites the issue missed, notices when the issue contradicts the repository, chooses the smallest change, runs the build, tests and lint, and reports the checks that failed or could not run instead of hiding them.

**How a leader knows it is working.** `/dashboard` (JSON at `/metrics`) answers:

| Question | Where to look |
|---|---|
| How many issues entered, and how many sessions were started? | Run count in the page header; one session per run |
| What is active, finished or failed? | "Running now" and "Failed" tiles; the outcome donut |
| How many PRs, and how fast? | PR link per run; median time to PR and to handoff |
| What did Devin conclude? | Outcome (`fixed`, `not_actionable`, `blocked`) and commands passed/total in the runs table |
| Did it need a person? | A `blocked` outcome, a `waiting_for_user` run with no outcome, or a run that is still `working` long after the others. Devin's questions are stored with the run and are in its session |
| What did it cost? | Not available. The API reports `0.0` ACUs for every session on this plan, and the org consumption endpoint reports `0.0` too |
| Was the PR accepted? | Not tracked. The service records Devin's report, not the reviewer's verdict; read PR state on GitHub |

These are observed results, not measured savings: there is no human baseline.

**What the evidence supports.**

| Kind | What it covers here |
|---|---|
| **Measured** | Time from event to PR and to handoff, per run; commands passed out of run; run counts by state and outcome; the diff of each PR |
| **Observed** | What Devin did on real runs: it caught claims the issue got wrong, found a copy the issue missed, and stopped on an issue that already had a PR |
| **Not claimed** | Engineer hours saved, cost per fix, or merge rate: there is no baseline, ACUs read `0.0`, and no PR has been merged by a reviewer through this system |
| **Proposed** | Merge rate, reviewer edits per PR and cost per fix as the numbers a real deployment should track; see [Expansion ideas](#expansion-ideas) |

**Who does what.** People still decide which issues are ready (they add the label) and still review and merge every PR. The automation covers the middle: environment setup, investigation, the change, the checks and the write-up. That is where the deferred time goes, and it is also where an agent's judgment is most useful.

## End to end flow

1. **A person labels an issue** `devin-remediate` in [sruthikilari/superset](https://github.com/sruthikilari/superset). GitHub sends an `issues` / `labeled` webhook to `POST /webhook`.
2. **The service gates the event.** It must be the right event, the trigger label, the target repo and an open issue, and the issue must have no run that Devin is still working on. Anything else is answered `200 ignored` with a reason.
3. **The service creates a Devin session** with the prompt, [sruthikilari/superset](https://github.com/sruthikilari/superset) as the repo, a usage cap and a structured-output schema, then records the run in SQLite with the session ID.
4. **Devin works on its own.** It checks the issue's claims against the code, looks for an existing PR, makes the smallest change, runs the validation the issue names, opens a PR against the fork's default branch, comments once on the issue, and returns a structured result.
5. **The poller follows the session** every 30 seconds, storing status, PR and result, until Devin stops working: it hands off to a person (`waiting_for_user`, `finished`), it fails, or the age limit is reached.
6. **A human reviews the PR** and merges it or not. The dashboard shows every run, its outcome and its timings.

## Architecture

```
GitHub issue labeled `devin-remediate`
        │  webhook (issues / labeled)
        ▼
┌──────────────────────── this service (FastAPI + SQLite, one container) ───────────────────────┐
│                                                                                              │
│  POST /webhook ─► gate ─► one active run per issue? ─► create Devin session ─► save run     │
│                                                                                              │
│  poller (30 s): read session ─► derive state ─► save status, PR, structured result           │
│                                                                                              │
│  GET /dashboard   /metrics   /remediations   /remediations/{id}/events   /healthz            │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
        │ create session, poll (Devin API v3)              ▲
        ▼                                                  │ opens PR, comments on the issue
   Devin session ─────────────────────────────────────────►  GitHub fork
```

The service never writes to GitHub. Devin opens the PR and posts the status comment itself, because the prompt tells it to. Dispatch, failures and timings live in the database and on the dashboard.

## Running this solution

### Prerequisites

- **Docker** with Compose.
- **A Devin org with API access** and a service-user token (starts with `cog_`) that can manage org sessions.
- **A fork to work on**, such as [sruthikilari/superset](https://github.com/sruthikilari/superset), with a label named `devin-remediate`.
- **Devin's GitHub app installed on that fork.** This is the only GitHub access the solution uses: Devin clones the repo, pushes a branch, opens the PR and comments on the issue through it, and the service itself holds no GitHub token. Install it from the GitHub integration settings in the Devin web app, grant it access to the fork only, and make sure it can read and write code, pull requests and issues. Without it, the session cannot open a PR or comment. Because the app is limited to the fork, Devin cannot open a PR against `apache/superset`.
- **A public URL** for the webhook, from a tunnel with a stable address (for example ngrok's free static domain).

### Environment variables

Copy `.env.example` to `.env` and fill it in. `.env` is git-ignored and is passed to the container by Docker Compose.

| Variable | Default | Purpose |
|---|---|---|
| `DEVIN_API_KEY` | required | Devin service-user token |
| `DEVIN_ORG_ID` | required | Devin organization ID, like `org-xxxxxxxx` |
| `TARGET_REPO` | required | `owner/repo` of the fork (here `sruthikilari/superset`). Events from any other repository are ignored |
| `MAX_ACU_LIMIT` | `15` | Per-session usage cap sent to Devin |
| `POLL_INTERVAL_SECONDS` | `30` | Poller cadence |
| `MAX_RUN_MINUTES` | `60` | A run still going after this long is marked `timed_out`; its session is left open |
| `DATABASE_PATH` | `./data/remediations.db` | SQLite file (Docker sets `/data/remediations.db`) |

### Configure the GitHub webhook

1. Start a tunnel to port 8000, for example `ngrok http --url=<your-domain>.ngrok-free.dev 8000`.
2. In [sruthikilari/superset](https://github.com/sruthikilari/superset), go to **Settings → Webhooks → Add webhook**:
   - **Payload URL:** `https://<your-tunnel>/webhook`
   - **Content type:** `application/json`
   - **Secret:** blank (signatures are not verified, so keep the URL private)
   - **Events:** select individual events, then only **Issues**
3. A green tick on GitHub's ping delivery means it is wired up (the service answers `pong`).

The label event fires only when the label is *added*. To trigger an issue again, remove the label and add it back.

### Run with Docker

```bash
cp .env.example .env        # then set DEVIN_API_KEY, DEVIN_ORG_ID and TARGET_REPO
docker compose up --build
```

- Dashboard: http://localhost:8000/dashboard. Health: http://localhost:8000/healthz.
- State is in `./data/remediations.db`, mounted into the container, so it survives restarts and rebuilds. Delete `data/remediations.db` to start over; the app recreates it.
- The container runs as a non-root user, restarts unless stopped, and has a health check. Secrets come from `.env` and are not in the image.

Without Docker: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, export the variables (`set -a; . ./.env; set +a`), then `.venv/bin/uvicorn app.main:create_app --factory --port 8000`.

**Trigger a run:** add the `devin-remediate` label to an open issue in [sruthikilari/superset](https://github.com/sruthikilari/superset) and watch the dashboard. In development a PR appeared in about 5 minutes and handoff took 4 to 10. This uses Devin quota.

**Without GitHub or a tunnel**, post the event yourself. This starts a real Devin session and a real PR on the fork, so it uses quota. Use an open issue of your own:

```bash
curl -X POST http://localhost:8000/webhook -H 'Content-Type: application/json' -H 'X-GitHub-Event: issues' -d '{
  "action": "labeled", "label": {"name": "devin-remediate"},
  "repository": {"full_name": "sruthikilari/superset"},
  "issue": {"number": 3, "state": "open", "title": "<issue title>", "body": "<issue body>",
            "html_url": "https://github.com/sruthikilari/superset/issues/3"}}'
```

Expect `202` with a `session_id`. Anything else is `200 ignored` with the reason, or `502` if Devin refused the session.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Container exits at start with "Missing required environment variables" | `.env` is missing `DEVIN_API_KEY`, `DEVIN_ORG_ID` or `TARGET_REPO`. The message names which |
| GitHub's webhook delivery shows `404` or a red cross | The payload URL must end in `/webhook`, and the tunnel must point at port 8000. Check `docker compose logs app` for the request |
| Labeling does nothing | A `200 ignored` response names the reason: wrong label, wrong repository, or a closed issue. The label event fires only when the label is *added*, so remove and re-add it |
| `502` on the webhook | Devin refused or could not be reached. The run is recorded with status `error` and the message. Check `DEVIN_API_KEY` and `DEVIN_ORG_ID`, then re-label |
| The run finishes in about a minute as `not_actionable` | The issue already has an open PR, and Devin found it and stopped. Close that PR to start a fresh attempt |
| A run stays `working` | Devin is still going. Open the session link on the dashboard to see what it is doing. The run is marked `timed_out` after `MAX_RUN_MINUTES` |
| A run shows `waiting_for_user` but no PR | Devin stopped to ask a question. Answer it in the Devin session; the service has stopped following the run, so its later progress is not shown |
| The dashboard shows a PR as open after it was merged | The PR state is recorded at handoff and is not refreshed. Check GitHub for the current state |
| Start over | Stop the container, delete `data/remediations.db`, and start it again |

## How Devin is configured

Everything Devin-specific is in one session-create call (`app/main.py`) and one prompt module (`app/prompt.py`).

| Setting | Value | Why |
|---|---|---|
| `prompt` | Fixed process, constraints and PR format; the issue body sits between markers as data | The process is set; the engineering decisions stay with Devin |
| `repos` | `[TARGET_REPO]` | Selects the repository for the session. What limits where Devin can push is the GitHub app's installation, which covers [sruthikilari/superset](https://github.com/sruthikilari/superset) only, so a PR cannot go to `apache/superset` |
| `max_acu_limit` | `MAX_ACU_LIMIT` (15) | A cap on spend per session |
| `structured_output_schema` | `outcome`, `summary`, `files_changed`, `commands_run`, `not_run`, `claims_verified`, `open_questions`, `pr_url` | Machine-readable result; feeds the dashboard |
| `structured_output_required` | `true` | Devin must return the result before it stops |
| `title` | `Remediate #<n>: <issue title>` | Findable in the Devin UI |

What the prompt asks Devin to do:

```
Investigate ─► check for an open PR from an earlier attempt ─► smallest change ─► run named validation
      │                     │
      │                     └─ found one: open no second PR, report not_actionable, cite it
      └─ a claim does not hold, or the fix needs a decision: stop and report (blocked)

Then: open one PR (base = the default branch of the fork) ─► comment once on the issue ─► return the structured result
```

## Status lifecycle

Each run is tracked by the status Devin reports for its session. On every poll the service reads the session's `status_detail` (or `status` when there is no detail), stores it, and shows it on the dashboard. It stops polling once Devin stops working, and it records the **outcome** from the structured result Devin returns. The only status the service adds itself is `timed_out`.

**What Devin reports**

| Devin status | Meaning | The service |
|---|---|---|
| `new`, `claimed`, `working` | Starting up, investigating, editing, testing | keeps polling |
| `waiting_for_user`, `waiting_for_approval` | Devin has stopped and needs a person, to review its PR or answer a question | stops polling, reads the PR and the outcome |
| `finished` | Devin considers the task done | stops polling, reads the PR and the outcome |
| `suspended` (`inactivity`), `exit` | The session ended or went idle | stops polling |
| `error`, or a usage or quota limit such as `out_of_quota` | The session failed | stops polling; the run counts as failed |
| `timed_out` | Still going after `MAX_RUN_MINUTES` (the one status the service adds) | stops polling; the run counts as failed |

Devin's `status` stays `running` through handoff, so the service reads `status_detail`. This was observed in real runs. "Stopped" means only that Devin is no longer working: a `waiting_for_user` run with no PR is a question waiting for an answer, and if a person answers it in Devin, the service does not see what happens next.

**What Devin concluded** is the `outcome` in its structured result:

| Outcome | Meaning | Success rate |
|---|---|---|
| `fixed` | Change made, validation run, PR opened | Counts as a success |
| `not_actionable` | Nothing to change, for example an earlier PR is already open | Left out |
| `blocked` | Devin stopped on a failed claim or a decision it should not make; see `open_questions` | Counts against |
| none | Devin stopped with no result, or the run failed | Counts against |

The rate is `fixed` over stopped runs, excluding `not_actionable`. It is Devin's own report, not proof the change is correct, so every PR still needs a person to read it.

**Typical run** (polled every 30 seconds):

| Time | Devin status | What the service records |
|---|---|---|
| 0:00 | `new` | Run created with the session ID |
| 0:30 to about 5:00 | `working` | Status; the PR link once it appears (`pr_state: open`) |
| 4 to 10 min | `waiting_for_user`, with the structured output | Status, outcome `fixed`, and the time Devin stopped |

The structured output holds `outcome` and `summary` (required), plus `files_changed`, `commands_run`, `not_run`, `claims_verified`, `open_questions` and `pr_url`. The schema is in `app/prompt.py`. Only `outcome` feeds the metrics; the dashboard also shows `commands_run` as passed/total.

Devin drafts this output while it works. In one observed run it wrote `outcome: blocked` five seconds in and changed it to `fixed` at about six minutes, both while still `working`. The API does not mark a snapshot as final, so the service keeps the output only from the snapshot taken when Devin stops.

## The dashboard

`GET /dashboard` is a server-rendered page (no JavaScript build) that shows:

- **Success rate:** runs where Devin reported `fixed`, out of finished runs, leaving out `not_actionable` runs (for example an issue that already has an open PR) and runs still going. It is Devin's own report, not proof the fix is right, so it is shown with its count.
- **Median time to PR** and **median time to handoff.**
- **Running now** and **failed** counts.
- **An outcomes donut** (fixed, no change needed, other, failed).
- **A table of runs** with the issue, state (with Devin's status and the outcome under it), timings, and links to the issue, the Devin session and the PR.
- **No cost or time saving is claimed.** There is no human baseline, and the API reported `0.0` ACUs for every run on this plan.

## API

| Endpoint | Purpose |
|---|---|
| `POST /webhook` | GitHub events. Only "trigger label added" starts a run |
| `GET /dashboard` | The HTML dashboard (`/` redirects here) |
| `GET /metrics` | Aggregate numbers |
| `GET /remediations` | All runs, newest first |
| `GET /remediations/{id}/events` | State changes for one run (`404` if unknown) |
| `GET /healthz` | Liveness |

All routes are defined in `app/main.py`, inside `create_app()`. The dashboard page is built by `app/dashboard.py` and the numbers by `app/metrics.py`.

Sample responses (values illustrative):

`POST /webhook`

```jsonc
// 202: label added, session started
{"status": "dispatched", "run": 1, "session_id": "3198...", "session_url": "https://app.devin.ai/sessions/3198..."}
// 200: not a trigger, or a run for this issue is already in progress
{"status": "ignored", "reason": "a run for this issue is already in progress"}
// 200: GitHub's ping
{"status": "pong"}
// 502: Devin refused or could not be reached; the run is recorded with status `error`
{"status": "error", "reason": "could not start Devin session"}
// 400: body is not a JSON object
```

`GET /metrics`

```json
{
  "total": 3, "active": 0, "finished": 3, "fixed": 2, "no_action": 1, "other": 0, "failed": 0,
  "rated": 2, "rate": 1.0,
  "seconds_to_pr": {"n": 2, "min": 316.2, "median": 401.0, "max": 486.0},
  "seconds_to_handoff": {"n": 3, "min": 75.8, "median": 349.9, "max": 624.0},
  "acus": {"total": 0.0, "runs_reporting_nonzero": 0}
}
```

`GET /remediations`

```json
{
  "count": 1,
  "remediations": [{
    "id": 1, "issue_number": 1, "attempt": 1, "devin_status": "waiting_for_user", "reason": null,
    "outcome": "fixed", "session_url": "https://app.devin.ai/sessions/3198...",
    "pr_url": "https://github.com/owner/repo/pull/7", "pr_state": "open",
    "acus_consumed": 0.0, "created_at": 1790356000.0, "completed_at": 1790356400.0
  }]
}
```

`GET /remediations/1/events`

```json
{
  "remediation_id": 1, "devin_status": "waiting_for_user",
  "events": [
    {"id": 1, "remediation_id": 1, "at": 1790356000.0, "from_status": null, "to_status": "new", "detail": null},
    {"id": 2, "remediation_id": 1, "at": 1790356400.0, "from_status": "new", "to_status": "working", "detail": null},
    {"id": 3, "remediation_id": 1, "at": 1790356400.0, "from_status": "working", "to_status": "waiting_for_user", "detail": null}
  ]
}
```

## Why Devin is the core of this solution

Remove Devin and what remains is a webhook, a timer and a table. Every step that needs judgment or engineering is a Devin capability, and the service is built around using them through the Devin API. 

| Devin capability | How this solution uses it | What it replaces |
|---|---|---|
| **Works from a plain-language issue** | The issue text is the whole task. Adding a new kind of fix means writing a new issue | A rule, script or template per kind of fix |
| **Reads the codebase and tests claims before editing** | Told to verify each claim in the issue against the code; it reports claims that do not hold and problems the issue missed | Trusting the issue blindly, or a human triaging first |
| **Its own environment** | Clones [sruthikilari/superset](https://github.com/sruthikilari/superset), installs dependencies and runs the validation the issue names, then reports what it could not run and why | CI wiring, a runner and setup scripts |
| **Judgment about when not to act** | Finds an existing PR and stops (`not_actionable`), or stops on a claim that fails or a decision it should not make (`blocked`), with open questions | Hard-coded skip rules; the service has one guard, not a rule set |
| **Structured output** | `structured_output_schema` returns a typed result (outcome, files changed, commands run and not run, claims checked, PR URL), and `structured_output_required` makes Devin return it | Scraping PR text or logs to learn what happened |
| **Acts on GitHub itself** | Opens the PR against [sruthikilari/superset](https://github.com/sruthikilari/superset) and posts the status comment, so the service holds no GitHub credentials and makes no GitHub calls | A GitHub client, tokens and comment logic in this service |
| **Sessions as a durable API object** | Create, read and poll by ID. Sessions outlive a service restart, so the poller resumes from SQLite | A job queue and worker state |
| **Built-in limits** | `max_acu_limit` caps usage per session; the GitHub app's installation scope limits which repositories it can push to | Budget tracking and repository allowlists in the service |
| **Human handoff** | Devin waits (`waiting_for_user`) when it needs input or review, and that state is visible through the API | A custom approval flow |

**What the code adds around it:** the trigger (a label), the record (SQLite), and visibility (the dashboard). Together that is about 450 lines of logic, and none of it does the engineering work.

**How it differs from other commonly used tools.** Dependency bots open PRs for version bumps and cannot make a code change such as an `md5` flag or a default credential. Scanners report findings and stop there. A script fixes the one case it was written for. Devin handles all of these from the issue text, and its structured result says what it did and did not verify. That is what lets a small service sit on top of it.

## Full automation roadmap

Today a person finds the issue and labels it. These steps can be fully automated by Devin as well: **Code Scans** to find issues, and **Devin Review** to review the PRs. The service in this repo stays the middle piece.

```
 Devin Code Scan                  this service (unchanged)                 Devin Review
 ───────────────                  ────────────────────────                 ────────────
 scheduled scan  ──► findings ──► file GitHub issue ──► label ──► Devin ──► PR opened ──► auto-review
 (POST /v3/.../code-scans,        with `devin-remediate`         session     │           (bugs, security,
  poll, read findings)            (a filter picks which)         fixes it    │            Auto-Fix suggestions)
        ▲                                                                    ▼                  │
        │                                                        dashboard: run, outcome         ▼
        └────────────── next scan sees the merged fix ◄──────────── human merges ◄── review findings
```

| Step | Devin tool | What changes in this repo |
|---|---|---|
| **1. Find** | Code Scans. The API starts a scan (`POST /v3/organizations/{org_id}/code-scans` with `repo_name`, `scan_type` such as `security` or `code-quality`, and `effort`), then lists its status and findings | A small scheduled job starts a scan, polls until `completed`, reads the findings, and drops the ones that are too big or too uncertain |
| **2. Ticket** | None. Each kept finding becomes a GitHub issue with the `devin-remediate` label, in the same format the current issues use | The job creates the issue. The webhook and everything after it stay as they are |
| **3. Fix** | Devin session, exactly as now | Nothing |
| **4. Review** | Devin Review reviews the PR when it is opened (or on `/devin review`), and can suggest fixes with Auto-Fix. Its output is PR comments, status checks and commit suggestions | Turn on auto-review for the fork. To use its verdict in the dashboard, read the PR's status checks from GitHub |
| **5. Merge** | A human, or an auto-merge rule for PRs whose review is clean | A policy decision. Auto-merge is not recommended until the review history is trusted |
| **6. Repeat** | The next scan reports the finding as gone, or reports it again if the fix was wrong | The job de-duplicates against issues that already exist |

## Expansion ideas

Ideas that make Devin more capable, connect it to more tools, or let the service run at larger scale. 

### Give Devin more to work with

| Idea | What it adds |
|---|---|
| **Skills** | Repo-specific instructions Devin loads when relevant, such as how to set up this repo's environment (for example `npx npm@11` for the frontend) and what its PR conventions are. |
| **Playbooks** | A saved, reusable prompt per kind of fix (dependency bump, security default, portability). The session-create call accepts `playbook_id`, so the service picks the playbook from the issue's labels and the inline prompt shrinks to the issue itself |
| **Knowledge** | Standing facts Devin recalls across sessions, such as past reviewer feedback or repo quirks. The create call accepts `knowledge_ids`. The docs mark Knowledge as deprecated and being migrated to Skills, so build on Skills first |
| **Follow-up messages** | Send Devin a message when CI fails or a reviewer comments, so it fixes its own PR instead of waiting for a human. The poller already sees the handoff state where this would start |

### Connect it to more tools

| Idea | What it adds |
|---|---|
| **MCP integrations** | Install a connector from Devin's marketplace, and Devin can read from and write to that tool during a session. Linear has native tools; Datadog and other official MCP servers are available. Devin could pull a Sentry error or a Datadog alert while investigating, or update a Jira or Linear ticket when it opens the PR |
| **Other triggers** | Start a session from a Linear or Jira ticket, a Sentry issue or a Datadog monitor, not only a GitHub label. Each source adds a small adapter that builds the prompt and calls the same session-create function |
| **Teams updates** | Post to a Teams channel when a run starts, hands off or fails, using an MCP connector or a webhook. The events table already records each state change |
| **GitHub Actions** | A workflow on `push` (or on a schedule) that calls the service or the Devin API directly, for example to run a Code Scan or start a session when a dependency file changes. Cognition's docs include an example workflow that calls Devin to fix failing CI on a PR |

### Scale it

| Scale | What changes |
|---|---|
| **1 repository** | As built |
| **10 repositories** | A repository allowlist, per-repo skills or playbooks, a per-repo filter on the dashboard, and a cap on concurrent sessions |
| **100 repositories** | A queue and a spend budget, Postgres in place of SQLite, signed webhooks and authenticated dashboards, and a merge-rate metric per team |


| Idea | What it adds |
|---|---|
| **Webhook signature check** | Verify `X-Hub-Signature-256` against a shared secret, then a collaborator check on whoever added the label. About 15 lines, and the first thing to add before real use, since the endpoint is open today |
| **Many repositories** | `TARGET_REPO` becomes an allowlist and each run stores its repo. Sessions already take a list of `repos`, so a fix that spans repos is possible. Add per-repo skills or playbooks and per-repo dashboard filters |
| **Claim-first dispatch** | The current lock holds for one process. To make it hold across processes or replicas, claim the issue in the database before calling Devin: insert the run with no session yet, backed by a unique index on the active issue (`repo`, `issue_number` where `completed_at IS NULL`), then create the session and attach its ID, or mark the run `error` if creation fails. The poller skips runs with no session yet, and a crash between the claim and the session leaves a run the age limit fails. The database decides who wins, so nothing depends on which process handled the event |
| **Merge signal** | Keep reading the PR's state after handoff, or listen for GitHub's `pull_request` event, so the dashboard can show merge rate and time to merge, the numbers a leader would trust most |
| **Concurrency cap and a queue** | Limit active sessions and hold extra label events until a slot frees, so a burst of labels or a scan cannot start dozens of paid sessions at once |
| **Resilient client** | Retries with backoff on 429 and 5xx, honoring `Retry-After`. Never retry a session creation after an ambiguous failure, since that could start a second billable session |
| **Crash recovery** | Record the run before creating the session, tag the session with the issue, and on restart look up sessions by tag instead of guessing, so a crash cannot leave a session with no run |
| **Session tags** | Tag each session with the repo, issue and fix type. That makes usage per team, and per kind of fix, easy to report |

## Project layout

```
app/
  main.py         FastAPI app and endpoints (webhook, dashboard, metrics, remediations, events, healthz); settings from environment variables
  devin.py        Devin v3 client: create and read a session
  prompt.py       session prompt and structured-output schema
  status.py       Devin's status vocabulary: when a session has stopped, and which statuses are failures
  poller.py       lifecycle loop (runs come from SQLite)
  db.py           SQLite: runs and an audit trail of state changes
  metrics.py      computes the numbers (called by the /dashboard and /metrics routes)
  dashboard.py    builds the dashboard HTML, with an SVG donut (called by the /dashboard route)
data/remediations.db  SQLite state
Dockerfile, docker-compose.yml, requirements.txt, .env.example
plan.md           short design summary
issues.md         the three issues filed on the fork
```
