# JobSearchAgent

JobSearchAgent is a local job discovery and triage system. It uses a persistent Playwright
browser session, local Ollama models, JEV typed decisions, and SQLite persistence.

The main command is `python -m jobsearchagent run`. It walks every `enabled: true` row in
`src/config/sites.yaml`, triages each new job, and queues it. The rows on today are RemoteOK,
Himalayas, and EuroTechJobs (keyword search and the remote category). LinkedIn stays in the
file and in `blocked.sites`, so this command does not open linkedin.com. Easy Apply fill
remains the LinkedIn path and runs only if that block is removed.

The current work order is [`src/docs/multisite_build_guide.md`](src/docs/multisite_build_guide.md).
The LinkedIn Easy Apply slice stays in
[`src/docs/v1_build_guide.md`](src/docs/v1_build_guide.md).

## Requirements

- **Python 3.11+**. SQLite ships with Python — no database server, account, or port. The optional
  `sqlite3` CLI is only needed if you want to inspect the file by hand.
- **Ollama 0.40.4+** running locally ([ollama.com/download](https://ollama.com/download)), exposing
  the JEV `POST /v1/systemone` endpoint.
- A LinkedIn account only if LinkedIn automation is turned back on. Login stays manual;
  credentials are never read or stored. The boards enabled today do not ask for an account.

## Installation

From the repository root:

```bash
python3 --version
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"

python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env
```

Activate `.venv` again whenever you open a new terminal for this project.

`.env` is Git-ignored. It controls models, the SQLite path, the Chromium profile path, rate
limits, `JOB_LOCATIONS`, and `TAILORED_CV_DIR` (default `/Users/macnolo/Desktop/Trabajo`).
Defaults create `~/.jobsearchagent/jobsearch.db` and `~/.jobsearchagent/chrome-profile` on first
run. Keep `DRY_RUN=true` while developing. The tailored-CV writer uses `WRITER_MODEL` (default
`qwen2.5:7b`), `WRITER_TIMEOUT_SECONDS=900`, and `WRITER_NUM_CTX=8192`.

In a separate terminal:

```bash
ollama serve                       # if not already running
ollama pull nimble                 # JEV decision model
ollama pull qwen2.5:7b             # generative model (writers / apply)
curl http://localhost:11434/api/tags
```

On CPU-only hardware, one JEV call can take several minutes per job.
`JEV_TIMEOUT_SECONDS=500` in `.env` is the timeout.

## Quickstart

With `.venv` active and Ollama running:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v

# One query
PYTHONPATH=src python -m jobsearchagent run --query "ServiceNow developer" --dry-run

# Every query in src/config/searches.yaml (tier 1 first)
PYTHONPATH=src python -m jobsearchagent run --dry-run

# Only exact (tier 1) yaml queries
PYTHONPATH=src python -m jobsearchagent run --tier 1 --dry-run

# Up to 15 new jobs per search, stop the run after 80 new jobs
PYTHONPATH=src python -m jobsearchagent run --dry-run --limit 15 --max-jobs 80

# Only jobs published in the last month
PYTHONPATH=src python -m jobsearchagent run --dry-run --limit 15 --max-jobs 80 --month 1
```

The enabled boards do not ask for a login. Locations default to `JOB_LOCATIONS` in `.env`
(Norway and Remote) and are used only when that row has `supports_location: true`. RemoteOK
and Himalayas open their category once. EuroTechJobs searches one token at a time
(`ServiceNow developer` becomes `servicenow`) and does not loop locations. Repeat `--location`
to override the list for one run. Repeat `--site` or `--skip-site` to narrow the enabled set.
`--limit` is new jobs **per search unit** (default 20). `--max-jobs` optionally caps new jobs
for the whole run. A full yaml run still means one JEV call per new job and can take hours.
To re-analyze jobs already in the database, clear them first
(see [Delete stored jobs](#delete-stored-jobs)).

`--dry-run` is the default posture for LinkedIn Easy Apply: fill, close without Submit, Save,
and queue. On every other site the job is triaged and the apply link is queued. Nothing is
submitted. `--easy-apply-only` and `--include-external` change the LinkedIn search only.
Repeat `--query` for a fixed list instead of the yaml file.

Search only, without opening Easy Apply:

```bash
PYTHONPATH=src python -m jobsearchagent discover --query "ServiceNow developer" --limit 20
```

## Local review UI

No account and no public bind. The UI reads `DB_PATH` through a localhost JSON API.

```bash
# Terminal 1 — JSON API on http://127.0.0.1:8765
PYTHONPATH=src python -m jobsearchagent serve

# Terminal 2 — UI on http://127.0.0.1:5173
cd frontend
npm install
npm run dev
```

`npm run dev` must run from `frontend/` (the repo root has no `dev` script).

- **Analyzed jobs** (`#jobs`) — `jobs` table.
- **Manual apply** (`#review`) — `review_queue`: open the LinkedIn link, then mark the row
  Applied or Rejected (or Reopen). Queue rows store `profile_id` when the matching job has one.

After `npm run build`, `serve` can also host `frontend/dist` at `http://127.0.0.1:8765`.

`run` and `discover` walk the enabled sites and refuse to open linkedin.com while LinkedIn is
blocked. `linkedin_agent`, the LinkedIn smoke test, and `tailor_cv` of a URL with no stored
description still open LinkedIn. To keep the account off that automation, follow
[`src/docs/linkedin_restriction_mitigations.md`](src/docs/linkedin_restriction_mitigations.md).
Which other boards are live, and which you still browse by hand:
[`src/docs/emea_remote_job_sources.md`](src/docs/emea_remote_job_sources.md).

## Search filters

These flags apply to `run` and `discover`. `--month` and `--work-type` follow the site row:
`native` or `url` builds the filter into the request, `post_filter` drops older or wrong-type
rows after the read, and `unsupported` skips that site for the run. A missing date on a
`post_filter` site is kept. `--geo-id`, `--posted-within`, `--sort`, and `--easy-apply` apply
only to LinkedIn. The table below is the LinkedIn mapping. `linkedin_agent` still uses that
mapping and still opens linkedin.com.

| Flag                               | Choices (default)                     | LinkedIn parameter                |
| ---------------------------------- | ------------------------------------- | --------------------------------- |
| `--geo-id`                         | region id, e.g. `91000007` (EMEA)     | `geoId` (replaces text location)  |
| `--posted-within`                  | `24h`, `week`\*, `month`              | `f_TPR`                           |
| `--month`                          | integer months, e.g. `1`              | `f_TPR` (overrides posted-within) |
| `--work-type`                      | `remote`\*, `hybrid`, `onsite`, `any` | `f_WT` (`any` removes it)         |
| `--sort`                           | `relevance`\*, `recent`               | `sortBy`                          |
| `--easy-apply` / `--no-easy-apply` | on\* (`linkedin_agent`)               | `f_AL`                            |

`--geo-id` runs one region-scoped search instead of iterating `--location`. Triage eligibility
still follows `JOB_LOCATIONS` / `--location`. `--month N` keeps jobs published in the last N
months (LinkedIn's date filter plus a skip if the listing date is older). `--limit` is per
query×location; `--max-jobs` is the optional run total. Each `run` prints counts for
discovered, duplicate, rejected, review, external, dry-run-ready, and applied.

A job is location-eligible when it is based in a configured place (Norway), explicitly permits
remote work from there, includes that place through a wider scope (EEA / Europe / Nordic), or is
genuinely worldwide remote. Restricted remote that excludes Norway is rejected; an ambiguous
remote scope needs confirmation.

## How it works

```text
enabled sites in sites.yaml (skip blocked)
  → one search unit per site, then query, then location, where the row supports them
  → scrape and normalize
  → deduplicate and persist
  → select one work profile
  → JEV: location eligibility + skills outcome
  → LinkedIn Easy Apply, only when that site is unblocked:
       fill → close without Submit → Save → review_queue
  → every other accept or needs_confirmation: review_queue with apply_url
  → or record reject / external / failed

Standalone (not on `run`): python -m jobsearchagent.tailor_cv
  → pending review_queue jobs, or one --profile + --url
  → Ollama rewrite + fact-check → PDF in TAILORED_CV_DIR
```

### Configuration and shared state

- `.env`-driven models, browser, database, rate limits, `JOB_LOCATIONS`, and `TAILORED_CV_DIR`.
- YAML for the site registry (`sites.yaml`), queries (`searches.yaml`), and work profiles.
  `sites_extended.yaml` is a backup, not an input.
- Typed `JobState`, form, field-answer, and artifact models.
- `JOB_LOCATIONS` drives JEV location eligibility, and search locations on rows that support them.

### Discovery

`run` and `discover` call `discover_enabled_sites`. Each enabled row uses the adapter named in
YAML (`remoteok`, `himalayas`, `eurotechjobs`, `linkedin`). A site with no adapter is logged
and skipped. One Playwright session covers the run. A failure on one site is logged and the
next site runs.

LinkedIn, when it is allowed again, stays in `jobsearchagent.discovery.linkedin`:

- Reuses a persistent Chromium profile and a manually authenticated LinkedIn session.
- Searches recent remote LinkedIn jobs by query and configured location.
- Extracts canonical URLs, LinkedIn job IDs, title, company, location, employment type, and
  description.
- Detects Easy Apply and preserves external application links.
- Applies configured title exclusions.
- Deduplicates within a run and against SQLite.
- Stops for login and LinkedIn security challenges instead of automating them.

### JEV triage

`jobsearchagent.triage.triage`:

- Selects exactly one work profile from the job title and distinctive skills (no JEV call).
- Asks JEV two questions: location eligibility, and skills outcome (hard blockers vs a viable
  core stack). Those two answers are merged into `accept` / `reject` / `needs_confirmation`.
- Accepts when a good part of the core stack is covered, or when uncovered core technologies are
  closely adjacent and learnable. Missing secondary skills do not reject.
- Rejects only confirmed hard blockers, or a location that is ineligible.

### Artifact requirements

`jobsearchagent.triage.requirements` assigns `["cv", "cover_letter"]` to accepted Easy Apply
jobs, skips rejected and external jobs, and does not call a model for that fixed v1 list.

### Apply graph (G1) and batch runner (G2)

`jobsearchagent.graph` walks one job: triage → requirements → Easy Apply fill → record.
External, reject, and `needs_confirmation` skip the form. A non-LinkedIn job is triaged and
then recorded with its apply link. `invoke_job(seed)` uses `SqliteSaver`
(`thread_id = {site_id}:{source_job_id}`) so a stopped thread can resume. Existing LinkedIn
threads stay `linkedin:{id}`.

The apply form uploads the existing profile PDF from `src/docs`. A standalone tailored-CV
agent can rewrite that source toward an offer (see [Tailored CVs](#tailored-cvs)). Cover
letters and JEV-scoring of artifacts are not implemented. The writer is not wired into `run`.

Routing:

- Rejected job → `skipped`
- Accepted Easy Apply job → `dry_run_ready` (filled, saved, queued)
- Unknown fact or ambiguous eligibility → `manual` and review queue
- External application → `external` and review queue
- Triage or pipeline failure → `failed` and review queue

### Persistence

SQLite stores normalized jobs, JEV answers, routing status, and application links. A job is
already seen when the same site and source id match, or the canonical listing URL matches, or
the canonical apply URL matches, or company + title + location match. The first saved row
wins. `review_queue` holds uncertain, external, failed, and parked-for-manual jobs without
blocking later jobs. Each review row has `url`, `reason`, `link`, `status`
(`pending` / `applied` / `rejected`), and `profile_id` (copied from `jobs` when the URLs match).

## Other commands

Triage only, no Easy Apply fill:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query ServiceNow \
  --location Norway \
  --location Remote \
  --max-jobs 5
```

Interactive discovery-and-triage smoke test (does not submit):

```bash
PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke \
  --query ServiceNow \
  --max-jobs 3
```

Fill one Easy Apply modal from `apply/decision.json`, close without Submit, Save, and queue.
Pass `--live` only for one real submission:

```bash
PYTHONPATH=src python -m jobsearchagent.apply.agent
PYTHONPATH=src python -m jobsearchagent.apply.agent --live
```

Inspect required artifacts for a job already in SQLite (never clicks Submit):

```bash
PYTHONPATH=src python -m jobsearchagent.triage.smoke_requirements \
  https://www.linkedin.com/jobs/view/4471047498/
```

Add `--browser` to walk the live Easy Apply form instead of the stored policy.
`--force-accepted-easy-apply` tests the accepted branch without changing the stored outcome.

## Tailored CVs

Standalone module. Does not open Easy Apply and does not Submit. Source PDFs live in
`src/docs` and are chosen from each profile’s `source_cvs`:

| Profile                 | Source PDF                                      |
| ----------------------- | ----------------------------------------------- |
| `servicenow-developer`  | `Manuel Alonso - Developer ServiceNow.pdf`      |
| `fullstack-developer`   | `Manuel Alonso - Developer Full stack.pdf`      |
| `frontend-developer`    | full-stack PDF (same layout, frontend headline) |
| `ai-developer`          | `Manuel Alonso - Developer AI.pdf`              |

One offer:

```bash
PYTHONPATH=src python -m jobsearchagent.tailor_cv \
  --profile fullstack-developer \
  --url https://www.linkedin.com/jobs/view/4468019259/
```

Every pending `review_queue` job that has a `profile_id` (uses the stored job description
when the URL matches; scrapes LinkedIn only if that text is missing):

```bash
PYTHONPATH=src python -m jobsearchagent.tailor_cv
```

Output: `{job-id} - {company} - {job-title}.pdf` under `TAILORED_CV_DIR`
(`/Users/macnolo/Desktop/Trabajo` by default). Override with `--output-dir`. The rewrite
keeps source job order and summary length, adds at most seven extra grounded skills, and
keeps the source headline. Facts must already be in the source CV or `work_profiles.yaml`.
`not_claimed` tools (for example embeddings or ITOM) are dropped. Each job can take several
minutes on CPU.

Pass both `--profile` and `--url`, or neither. One flag alone is an error.

## Connect to the SQLite database

There is no database server. The default path is in `.env`:

```ini
DB_PATH=~/.jobsearchagent/jobsearch.db
```

```bash
sqlite3 ~/.jobsearchagent/jobsearch.db
```

```sql
.headers on
.mode column
.tables
PRAGMA table_info(jobs);
PRAGMA table_info(review_queue);
```

```sql
SELECT discovered_at, title, company, location, outcome, apply_status
FROM jobs
ORDER BY discovered_at DESC
LIMIT 20;

SELECT title, company, location, apply_status, url
FROM jobs
WHERE outcome = 'accept';

SELECT created_at, profile_id, status, reason, link
FROM review_queue
WHERE status = 'pending'
ORDER BY created_at DESC;

SELECT COALESCE(outcome, 'not_triaged') AS result, COUNT(*) AS total
FROM jobs
GROUP BY outcome
ORDER BY total DESC;
```

Exit with `.quit`. If `DB_PATH` was changed, pass that path to `sqlite3` instead.

## Delete stored jobs

```bash
PYTHONPATH=src python scripts/delete_jobs.py
```

The script shows how many rows will be removed and requires typing `DELETE`. By default it
clears both `jobs` and `review_queue`.

```bash
PYTHONPATH=src python scripts/delete_jobs.py --keep-review-queue
PYTHONPATH=src python scripts/delete_jobs.py --yes
PYTHONPATH=src python scripts/delete_jobs.py --db-path /path/to/jobsearch.db
```

## Not implemented yet

- Cover letters and JEV-scoring of generated artifacts (A5).
- Wiring `tailor_cv` into `run` / Easy Apply upload.

## Documentation

Current behavior is this README. The build guides are the work orders. The two prompts are the
briefs those guides closed.

| Doc | What it is |
| --- | --- |
| [`src/docs/multisite_build_guide.md`](src/docs/multisite_build_guide.md) | Current discovery work order. Parts M1–M6 are done. Part M7 is one adapter at a time; RemoteOK is the first. |
| [`src/docs/v1_build_guide.md`](src/docs/v1_build_guide.md) | LinkedIn Easy Apply work order. It does not describe the current `run`. |
| [`src/docs/tailored_cv_guide.md`](src/docs/tailored_cv_guide.md) | Standalone CV writer. Not on `run`. |
| [`src/docs/linkedin_restriction_mitigations.md`](src/docs/linkedin_restriction_mitigations.md) | How to keep Playwright off linkedin.com. |
| [`src/docs/emea_remote_job_sources.md`](src/docs/emea_remote_job_sources.md) | Board list: which rows `run` reads, and which you still open yourself. |
| [`src/docs/jev_ollama_agent_guide.md`](src/docs/jev_ollama_agent_guide.md) | How the local JEV decision calls are shaped. |
| [`src/docs/architechture_prompt.md`](src/docs/architechture_prompt.md) | Original design brief. The two build guides win where they disagree. |
| [`src/docs/Multisite agents prompt.md`](src/docs/Multisite%20agents%20prompt.md) | Planning brief for multi-site discovery. Closed by the multisite build guide. |
