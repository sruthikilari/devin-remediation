# Devin remediation automation

Label a GitHub issue `devin-remediate` and a Devin session investigates it, fixes it, validates the fix and opens a pull request. This service starts the session, follows it to a conclusion and shows what happened on a dashboard. A human reviews every PR; nothing is merged automatically.

It was built against [sruthikilari/superset](https://github.com/sruthikilari/superset), a fork of [apache/superset](https://github.com/apache/superset). Three real issues on that repo (a `md5` portability fix, default SMTP credentials, and a `js-yaml` dependency vulnerability) were filed for Devin to remediate through this automation. They are listed in [issues.md](issues.md).

📚 **Contents:** [Why it matters](#-why-this-matters) · [End to end flow](#-end-to-end-flow) · [Architecture](#-architecture) · [Running this solution](#-running-this-solution) · [Status lifecycle](#-status-lifecycle) · [Dashboard](#-the-dashboard) · [API](#-api) · [Why Devin](#-why-devin-is-the-core-of-this-solution) · [Roadmap](#-full-automation-roadmap) · [Expansion](#-expansion-ideas) · [Layout](#-project-layout)

## 🎯 Why this matters

⏳ **The problem.** Small fixes like a dependency advisory or an insecure default often pile up as tech debt that never gets prioritized, but still matters for security and maintenance. This system hands each one to Devin, which investigates it and opens a reviewable PR without waiting on anyone's backlog.

🤖 **Why Devin and not a script.** A script can make the edit it was written for, but a safe fix takes judgment: the issue has to be checked against the code, every affected site found, and the right checks run. Devin does that work itself. It verifies the issue's claims, finds sites the issue missed, notices when the issue contradicts the repository, chooses the smallest change, runs the build, tests and lint, and reports the checks that failed or could not run instead of hiding them.

## 🔄 End to end flow

1. **A person labels an issue** `devin-remediate` in [sruthikilari/superset](https://github.com/sruthikilari/superset). GitHub sends an `issues` / `labeled` webhook to `POST /webhook`.
2. **The service gates the event.** It must be the right event, the trigger label, the target repo and an open issue, and the issue must have no run that Devin is still working on. Anything else is answered `200 ignored` with a reason.
3. **The service creates a Devin session** with the prompt, [sruthikilari/superset](https://github.com/sruthikilari/superset) as the repo, a usage cap and a structured-output schema, then records the run in SQLite with the session ID.
4. **Devin works on its own.** It checks the issue's claims against the code, looks for an existing PR, makes the smallest change, runs the validation the issue names, opens a PR against the fork's default branch, comments once on the issue, and returns a structured result.
5. **The poller follows the session** every 30 seconds, storing status, PR and result, until Devin stops working: it hands off to a person (`waiting_for_user`, `finished`), it fails, or the age limit is reached.
6. **A human reviews the PR** and merges it or not. The dashboard shows every run, its outcome and its timings.

## 🧩 Architecture

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

## 🚀 Running this solution

### ✅ Prerequisites

- **Docker** with Compose.
- **A Devin org with API access** and a service-user token (starts with `cog_`) that can manage org sessions.
- **A fork to work on**, such as [sruthikilari/superset](https://github.com/sruthikilari/superset), with a label named `devin-remediate`.
- **Devin's GitHub app installed on that fork.** This is the only GitHub access the solution uses: Devin clones the repo, pushes a branch, opens the PR and comments on the issue through it, and the service itself holds no GitHub token. Install it from the GitHub integration settings in the Devin web app, grant it access to the fork only, and make sure it can read and write code, pull requests and issues. Without it, the session cannot open a PR or comment. Because the app is limited to the fork, Devin cannot open a PR against `apache/superset`.
- **A public URL** for the webhook, from a tunnel with a stable address (for example ngrok's free static domain).

### 🔑 Environment variables

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

### 🔗 Configure the GitHub webhook

1. Start a tunnel to port 8000, for example `ngrok http --url=<your-domain>.ngrok-free.dev 8000`.
2. In [sruthikilari/superset](https://github.com/sruthikilari/superset), go to **Settings → Webhooks → Add webhook**:
   - **Payload URL:** `https://<your-tunnel>/webhook`
   - **Content type:** `application/json`
   - **Secret:** blank (signatures are not verified, so keep the URL private)
   - **Events:** select individual events, then only **Issues**
3. A green tick on GitHub's ping delivery means it is wired up (the service answers `pong`).

The label event fires only when the label is *added*. To trigger an issue again, remove the label and add it back.

### 🐳 Run with Docker

```bash
cp .env.example .env        # then set DEVIN_API_KEY, DEVIN_ORG_ID and TARGET_REPO
docker compose up --build
```

The dashboard is at http://localhost:8000/dashboard. State is kept in `./data/remediations.db` (mounted into the container, so it survives restarts; delete it to start over). The container runs as a non-root user with a health check, and secrets come from `.env`, not the image.

Without Docker: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, export the variables (`set -a; . ./.env; set +a`), then `.venv/bin/uvicorn app.main:create_app --factory --port 8000`.

▶️ **Trigger a run:** add the `devin-remediate` label to an open issue in [sruthikilari/superset](https://github.com/sruthikilari/superset) and watch the dashboard. In development a PR appeared in about 5 minutes and handoff took 4 to 10. This uses Devin quota.

🧪 **Without GitHub or a tunnel**, post the event yourself. This starts a real Devin session and a real PR on the fork, so it uses quota. Use an open issue of your own:

```bash
curl -X POST http://localhost:8000/webhook -H 'Content-Type: application/json' -H 'X-GitHub-Event: issues' -d '{
  "action": "labeled", "label": {"name": "devin-remediate"},
  "repository": {"full_name": "sruthikilari/superset"},
  "issue": {"number": 3, "state": "open", "title": "<issue title>", "body": "<issue body>",
            "html_url": "https://github.com/sruthikilari/superset/issues/3"}}'
```

Expect `202` with a `session_id`. Anything else is `200 ignored` with the reason, or `502` if Devin refused the session.

### 🔧 Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Container exits at start with "Missing required environment variables" | `.env` is missing `DEVIN_API_KEY`, `DEVIN_ORG_ID` or `TARGET_REPO`. The message names which |
| Webhook delivery shows `404`, or labeling does nothing | The payload URL must end in `/webhook` and the tunnel must point at port 8000. A `200 ignored` response names the reason (label, repository, closed issue), and the label event fires only when the label is *added*, so remove and re-add it |
| `502` on the webhook | Devin refused or could not be reached. The run is recorded with status `error`. Check `DEVIN_API_KEY` and `DEVIN_ORG_ID`, then re-label |
| A run ends in about a minute as `not_actionable` | The issue already has an open PR and Devin stopped. Close that PR to start a fresh attempt |
| A run stays `working`, or shows `waiting_for_user` with no PR | Open its session link on the dashboard. Devin is either still going (`timed_out` after `MAX_RUN_MINUTES`) or asking a question, which you answer in Devin; the service has stopped following that run |

## 🚦 Status lifecycle

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

The structured output holds `outcome` and `summary` (required), plus `files_changed`, `commands_run`, `not_run`, `claims_verified`, `open_questions` and `pr_url`. The schema is in `app/prompt.py`. Only `outcome` feeds the metrics; the dashboard also shows `commands_run` as passed/total.

## 📊 The dashboard

`GET /dashboard` is a server-rendered page (no JavaScript build) that shows:

- **Success rate:** runs where Devin reported `fixed`, out of finished runs, leaving out `not_actionable` runs (for example an issue that already has an open PR) and runs still going. It is Devin's own report, not proof the fix is right, so it is shown with its count.
- **Median time to PR** and **median time to handoff.**
- **Running now** and **failed** counts.
- **An outcomes donut** (fixed, no change needed, other, failed).
- **A table of runs** with the issue, Devin's status (with the outcome under it), timings, and links to the issue, the Devin session and the PR. A run that needs a person shows as `blocked`, or as `waiting_for_user` with no outcome.
- **No cost or time saving is claimed.** There is no human baseline, and the API reported `0.0` ACUs for every run on this plan.
- **Not tracked:** whether a reviewer accepted the PR. The service records Devin's report, not the reviewer's verdict, so read the PR's state on GitHub.

## 🔌 API

| Endpoint | Purpose |
|---|---|
| `POST /webhook` | GitHub events. Only "trigger label added" starts a run |
| `GET /dashboard` | The HTML dashboard (`/` redirects here) |
| `GET /metrics` | Aggregate numbers |
| `GET /remediations` | All runs, newest first |
| `GET /remediations/{id}/events` | State changes for one run (`404` if unknown) |
| `GET /healthz` | Liveness |

All routes are defined in `app/main.py`, inside `create_app()`. The dashboard page is built by `app/dashboard.py` and the numbers by `app/metrics.py`.

## 🤖 Why Devin is the core of this solution

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

### 🎛 How the session is configured

What makes Devin reliable here is the context and environment it is given, and all of it is set in one session-create call (`app/main.py`) and one prompt module (`app/prompt.py`). The service supplies the goal, the constraints and the definition of done, and leaves the engineering decisions to Devin.

| Setting | Value | Why |
|---|---|---|
| `prompt` | Fixed process, constraints and PR format, plus the fork's setup skill (`@skills:superset-setup-and-test`); the issue body sits between markers as data | The process is set; the engineering decisions stay with Devin |
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

🪶 **What the code adds around it:** the trigger (a label), the record (SQLite), and visibility (the dashboard). Together that is about 450 lines of logic, and none of it does the engineering work.

**How it differs from other commonly used tools.** Dependency bots open PRs for version bumps and cannot make a code change such as an `md5` flag or a default credential. Scanners report findings and stop there. A script fixes the one case it was written for. Devin handles all of these from the issue text, and its structured result says what it did and did not verify. That is what lets a small service sit on top of it.

## 🧭 Full automation roadmap

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

1. **Find:** a small scheduled job starts a Code Scan through the API (`POST /v3/organizations/{org_id}/code-scans`, with `scan_type` such as `security` or `code-quality`), polls until it completes, and reads the findings, dropping the ones that are too big or too uncertain.
2. **Ticket:** each kept finding becomes a GitHub issue with the `devin-remediate` label. The webhook and everything after it stay as they are.
3. **Fix:** a Devin session, exactly as today.
4. **Review:** Devin Review reviews the PR when it opens and can suggest fixes with Auto-Fix. Turn on auto-review for the fork; to show its verdict on the dashboard, read the PR's status checks from GitHub.
5. **Merge and repeat:** a person merges, and the next scan reports the finding gone or reports it again if the fix was wrong. The job de-duplicates against issues that already exist.

## 💡 Expansion ideas

Ideas that make Devin more capable, connect it to more tools, or let the service run at larger scale.

| Idea | What it adds |
|---|---|
| **Skills and playbooks** | Repo-specific instructions Devin loads when relevant, and a saved prompt per kind of fix. A first skill, `superset-setup-and-test` (setup steps, which checks to run, how to report them), is in the fork at `.agents/skills/`; Devin reads skills from the repo it clones, so it lives there, not here. The session prompt invokes it with `@skills:superset-setup-and-test`. The session-create call accepts `playbook_id`, so the service can pick a playbook from the issue's labels and shrink the inline prompt to the issue itself. Devin's Knowledge feature is being migrated to Skills, so build on Skills first |
| **Follow-up messages** | Send Devin a message when CI fails or a reviewer comments, so it fixes its own PR instead of waiting for a person. The poller already sees the handoff where this would start |
| **MCP integrations** | Install a connector from Devin's marketplace (Linear, Datadog and others) so Devin can read a Sentry error or a Datadog alert while investigating, update a Jira or Linear ticket when it opens the PR, or post run updates to a Teams channel |
| **Other triggers** | Start a session from a Linear or Jira ticket, a Sentry issue, a Datadog monitor, or a GitHub Actions workflow on `push`, not only a label. Each source is a small adapter that builds the prompt and calls the same session-create function |
| **Webhook signature check** | Verify `X-Hub-Signature-256` against a shared secret, then check who added the label. About 15 lines, and the first thing to add before real use, since the endpoint is open today |
| **Many repositories** | `TARGET_REPO` becomes an allowlist and each run stores its repo, with per-repo skills and dashboard filters. Sessions already take a list of `repos`, so a fix can span repositories |
| **Queue and claim-first dispatch** | A cap on active sessions, with extra label events held until a slot frees, so a burst cannot start dozens of paid sessions. The current lock holds for one process; across replicas, claim the issue in the database before calling Devin (insert the run with no session, backed by a unique index on the active issue), then attach the session ID, so the database decides who wins |
| **Merge signal** | Keep reading the PR's state after handoff, or listen for GitHub's `pull_request` event, so the dashboard can show merge rate and time to merge, the numbers a leader would trust most |

## 📁 Project layout

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
