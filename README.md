# JobSearchAgent

JobSearchAgent is a local, LinkedIn-focused job discovery and triage system. It uses a persistent
Playwright browser session, local Ollama models, JEV typed decisions, and SQLite persistence.

The main command is `python -m jobsearchagent run`: search LinkedIn, triage each new job, fill
Easy Apply, close without Submit, Save the posting, and queue it for manual apply.

## Requirements

- **Python 3.11+**. SQLite ships with Python — no database server, account, or port. The optional
  `sqlite3` CLI is only needed if you want to inspect the file by hand.
- **Ollama 0.40.4+** running locally ([ollama.com/download](https://ollama.com/download)), exposing
  the JEV `POST /v1/systemone` endpoint.
- A LinkedIn account. Login is always completed manually in the browser; credentials are never
  read or stored.

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
limits, and `JOB_LOCATIONS`. Defaults create `~/.jobsearchagent/jobsearch.db` and
`~/.jobsearchagent/chrome-profile` on first run. Keep `DRY_RUN=true` while developing.

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
```

On the first run, complete LinkedIn login in the Chromium window when prompted, then return to
the terminal and press Enter. Locations default to `JOB_LOCATIONS` in `.env` (Norway and Remote).
Repeat `--location` to override them for one run. `--limit` caps **new** jobs across all searches
in that run. To re-analyze jobs already in the database, clear them first (see
[Delete stored jobs](#delete-stored-jobs)).

`--dry-run` fills Easy Apply, closes without Submit, saves the posting, and queues it for
manual apply. `--easy-apply-only` is the default; `--include-external` also records jobs that
open an employer ATS URL (those are never filled). Repeat `--query` for a fixed list instead of
the yaml file.

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

- **Analyzed jobs** (`#jobs`) — `jobs` table.
- **Manual apply** (`#review`) — `review_queue`: open the LinkedIn link, then mark the row done.

After `npm run build`, `serve` can also host `frontend/dist` at `http://127.0.0.1:8765`.

## Search filters

These flags apply to `run`, `discover`, and `linkedin_agent`. Defaults are remote, Easy Apply,
posted in the last week, sorted by relevance.

| Flag                               | Choices (default)                     | LinkedIn parameter               |
| ---------------------------------- | ------------------------------------- | -------------------------------- |
| `--geo-id`                         | region id, e.g. `91000007` (EMEA)     | `geoId` (replaces text location) |
| `--posted-within`                  | `24h`, `week`\*, `month`              | `f_TPR`                          |
| `--work-type`                      | `remote`\*, `hybrid`, `onsite`, `any` | `f_WT` (`any` removes it)        |
| `--sort`                           | `relevance`\*, `recent`               | `sortBy`                         |
| `--easy-apply` / `--no-easy-apply` | on\* (`linkedin_agent`)               | `f_AL`                           |

`--geo-id` runs one region-scoped search instead of iterating `--location`. Triage eligibility
still follows `JOB_LOCATIONS` / `--location`. Each `run` prints counts for discovered, duplicate,
rejected, review, external, dry-run-ready, and applied.

A job is location-eligible when it is based in a configured place (Norway), explicitly permits
remote work from there, includes that place through a wider scope (EEA / Europe / Nordic), or is
genuinely worldwide remote. Restricted remote that excludes Norway is rejected; an ambiguous
remote scope needs confirmation.

## How it works

```text
LinkedIn search (one query, or every query in searches.yaml)
  → scrape and normalize
  → deduplicate and persist
  → select one work profile
  → JEV: location eligibility + skills outcome
  → Easy Apply fill → close without Submit → Save → review_queue
  → or record reject / external / failed
```

### Configuration and shared state

- `.env`-driven models, browser, database, rate limits, and `JOB_LOCATIONS`.
- YAML for LinkedIn search URLs (`sites.yaml`), queries (`searches.yaml`), and work profiles.
- Typed `JobState`, form, field-answer, and artifact models.
- `JOB_LOCATIONS` drives both LinkedIn search locations and JEV location eligibility.

### LinkedIn discovery

`jobsearchagent.discovery.linkedin`:

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
External, reject, and `needs_confirmation` skip the form. `invoke_job(seed)` uses `SqliteSaver`
(`thread_id = linkedin:{source_job_id}`) so a stopped thread can resume.

CV and cover-letter writers (A4) and the validator (A5) are omitted. The form uploads the
existing profile PDF from `src/docs`.

Routing:

- Rejected job → `skipped`
- Accepted Easy Apply job → `dry_run_ready` (filled, saved, queued)
- Unknown fact or ambiguous eligibility → `manual` and review queue
- External application → `external` and review queue
- Triage or pipeline failure → `failed` and review queue

### Persistence

SQLite stores normalized jobs, JEV answers, routing status, and application links. Stable
LinkedIn IDs deduplicate URL variants. `review_queue` holds uncertain, external, failed, and
parked-for-manual jobs without blocking later jobs.

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

SELECT created_at, reason, link
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

- Generate a tailored CV and cover letter (A4 GEN) and JEV-score them (A5).

See `src/docs/v1_build_guide.md` for remaining implementation phases.
