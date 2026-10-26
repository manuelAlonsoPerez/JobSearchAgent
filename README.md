# JobSearchAgent

JobSearchAgent is a local, LinkedIn-focused job discovery and triage system. It uses a persistent
Playwright browser session, local Ollama models, JEV typed decisions, and SQLite persistence.

## Requirements

- **Python 3.11+** (SQLite ships with Python — no database server needed).
- **Ollama 0.40.4+** running locally ([ollama.com/download](https://ollama.com/download)), exposing
  the JEV `POST /v1/systemone` endpoint.
- A LinkedIn account (login is always completed manually in the browser; credentials are never read
  or stored by the app).

## Installation

From the repository root:

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# 2. Install Python dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Install the Playwright Chromium runtime
python -m playwright install chromium

# 4. Create the local environment file (safe defaults, Git-ignored)
cp .env.example .env

# 5. Start Ollama (separate terminal) and pull the configured models
ollama serve                       # if not already running
ollama pull nimble                 # JEV decision model
ollama pull qwen2.5:7b             # generative model (writers/apply)
```

`.env` controls models, the SQLite path, the Chromium profile path, rate limits, and
`JOB_LOCATIONS`. The defaults create `~/.jobsearchagent/jobsearch.db` and
`~/.jobsearchagent/chrome-profile` on first run. Keep `DRY_RUN=true` while developing. See
`setup.md` for the full, step-by-step setup and browser-login notes.

## Quickstart

With `.venv` active and Ollama running:

```bash
# Verify the setup (should pass)
PYTHONPATH=src python -m unittest discover -s tests -v

# Confirm Ollama is reachable
curl http://localhost:11434/api/tags

# Run the discovery-and-triage agent (does NOT submit applications)
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query developer \
  --max-jobs 10 \
  --location Norway \
  --location Remote
```

On the first run, complete LinkedIn login in the Chromium window when prompted, then return to the
terminal and press Enter. Locations default to `JOB_LOCATIONS` in `.env`; repeat `--location` to
override them for one run. On CPU-only hardware the one JEV evaluation call can still take a
few minutes per job — the agent prints heartbeat progress while it works. To re-analyze jobs
already in the database, clear them first with `python -m scripts.delete_jobs` (see
[Delete stored jobs](#delete-stored-jobs)).

## Functional parts developed so far

### Configuration and shared state

- `.env`-driven model, browser, database, rate-limit, and job-location settings.
- YAML configuration for LinkedIn search URLs, search filters, and candidate work profiles.
- Typed `JobState`, form, field-answer, and artifact models for the planned agent pipeline.
- `JOB_LOCATIONS` controls both LinkedIn search locations and JEV location eligibility.

### LinkedIn discovery

`jobsearchagent.discovery.linkedin` currently:

- Reuses a persistent Chromium profile and a manually authenticated LinkedIn session.
- Searches recent remote LinkedIn jobs by query and configured location.
- Processes the configured number of result pages.
- Extracts canonical job URLs, LinkedIn job IDs, title, company, location, and full description.
- Detects LinkedIn Easy Apply and preserves external application links.
- Applies configured title exclusions.
- Deduplicates jobs within a run and against jobs already stored in SQLite.
- Stops safely for login and LinkedIn security challenges rather than automating them.

### JEV triage

`jobsearchagent.triage.triage`:

- Selects exactly one candidate work profile from the job title and distinctive
  skills (no JEV call), then asks JEV only about that profile.
- Evaluates whether the configured locations permit applying.
- Accepts when a good part of the core stack is covered, or when uncovered core technologies are
  closely adjacent/learnable given the candidate's multi-year breadth.
- Reduces fit for missing secondary skills (and a minority of adjacent core gaps) without rejecting
  for those gaps alone.
- Rejects only confirmed hard blockers (most of the core absent and not adjacent, a confirmed
  constraint, or experience clearly far below an explicit minimum).
- Returns `accept`, `reject`, or `needs_confirmation` with a fit score.

### Artifact requirements

`jobsearchagent.triage.requirements`:

- Assigns `["cv", "cover_letter"]` to accepted LinkedIn Easy Apply jobs.
- Avoids preparing artifacts for rejected or external jobs.
- Performs no additional model call for the fixed v1 requirements.
- Provides independent JEV `noul` gates for optional future artifacts.
- Offers a browser inspection mode that walks the live Easy Apply form and
  requires a `cover_letter` only when the form exposes such a field.

Manually test A3 with a persisted LinkedIn URL or job ID:

```bash
PYTHONPATH=src python -m jobsearchagent.triage.smoke_requirements \
  https://www.linkedin.com/jobs/view/4471047498/
```

The URL is only used to load the normalized output of discovery and triage from SQLite; the job
is not scraped or analyzed again. A rejected job correctly returns no required artifacts. To test
the accepted Easy Apply branch without changing its stored outcome:

```bash
PYTHONPATH=src python -m jobsearchagent.triage.smoke_requirements \
  4471047498 \
  --force-accepted-easy-apply
```

To detect the required artifacts from the **live** Easy Apply form instead of the deterministic
policy, add `--browser`. It opens the modal, walks each step without submitting, and reports which
artifacts the form actually asks for:

```bash
PYTHONPATH=src python -m jobsearchagent.triage.smoke_requirements \
  https://www.linkedin.com/jobs/view/4471047498/ \
  --browser
```

A job whose Easy Apply flow only uploads a resume reports `["cv"]`; a `cover_letter` is added only
when the form exposes a corresponding field. The runner never clicks **Submit application**.

### Production LinkedIn agent

`jobsearchagent.linkedin_agent` combines discovery, JEV triage, artifact requirements,
persistence, and non-blocking review routing:

```text
LinkedIn search
  → scrape and normalize
  → deduplicate and persist
  → select one work profile
  → evaluate location, outcome, and fit
  → determine required application artifacts
  → persist result or add a review-queue item
```

Current routing is:

- Rejected job → `skipped`
- Accepted LinkedIn Easy Apply job → `dry_run_ready`
- Unknown fact or ambiguous eligibility → `manual` and review queue
- External application → `external` and review queue
- Triage failure → `failed` and review queue

Run the current production slice from the repository root:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query React \
  --max-jobs 5
```

Locations default to `JOB_LOCATIONS`. Override them for one run by repeating `--location`:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query ServiceNow \
  --location Norway \
  --location Remote \
  --max-jobs 5
```

#### Search filters

Flags shape the LinkedIn search URL. Defaults reproduce the historical
behavior: remote, Easy Apply, posted in the last week, sorted by relevance.

| Flag                               | Choices (default)                     | LinkedIn parameter               |
| ---------------------------------- | ------------------------------------- | -------------------------------- |
| `--geo-id`                         | region id, e.g. `91000007` (EMEA)     | `geoId` (replaces text location) |
| `--posted-within`                  | `24h`, `week`\*, `month`              | `f_TPR`                          |
| `--work-type`                      | `remote`\*, `hybrid`, `onsite`, `any` | `f_WT` (`any` removes it)        |
| `--sort`                           | `relevance`\*, `recent`               | `sortBy`                         |
| `--easy-apply` / `--no-easy-apply` | on\*                                  | `f_AL`                           |

To reproduce an EMEA ServiceNow search (last week, remote, Easy Apply):

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query ServiceNow \
  --geo-id 91000007 \
  --max-jobs 5
```

`--geo-id` runs one region-scoped search instead of iterating `--location`
text searches. Note that **triage eligibility still follows
`JOB_LOCATIONS`/`--location`**, so searching a wider region will not by itself
mark those jobs eligible — adjust `JOB_LOCATIONS` if you want EMEA jobs to pass
the location check.

### Persistence and review queue

- SQLite stores normalized jobs, JEV outcomes, fit scores, routing status, and application links.
- Stable LinkedIn IDs deduplicate URL variants and repeated searches.
- `review_queue` records uncertain, external, and failed jobs without blocking later jobs.
- A SQLite LangGraph checkpointer is available for the future thread-per-job graph.

### Supporting services

- `jev.py`: typed Ollama System One decisions (`choice`, `noul`, and `score`).
- `llm.py`: local Ollama chat client for future CV, cover-letter, and form-answer generation.
- `browser.py`: persistent browser sessions, accessible page text, form serialization, and uploads.
- `utils/profile.py`: loads exactly one selected profile and exposes grounded profile facts.
- `utils/pdf.py`: renders generated plain-text artifacts as A4 PDFs.
- `linkedin_smoke.py`: diagnostic scraper/JEV runner retained for testing and troubleshooting.

### Easy Apply filling (smoke)

`jobsearchagent.apply.smoke_apply` opens one job URL, fills profile-backed Easy
Apply fields, uploads the existing source CV when a resume field is present,
and **stops on Review**. It never clicks **Submit application**. Cover letters
and tailored CVs are skipped in this pass; CEFR, residence, English-score, and
unknown screening questions are deferred rather than guessed.

```bash
PYTHONPATH=src python -m jobsearchagent.apply.smoke_apply \
  https://www.linkedin.com/jobs/view/4478685620/
```

Optional: `--profile-id servicenow-developer`, `--cv /path/to/cv.pdf`. The
browser stays on Review until you press Enter, then the draft is discarded.

## Not implemented yet

The current production command does **not** apply for jobs. These build-guide stages remain:

- Generate a tailored CV and cover letter.
- Validate generated artifacts against profile facts and JEV quality thresholds.
- Submit the prepared Easy Apply modal (A7) or record the result (A8).
- Wire the complete thread-per-job workflow through LangGraph.

See `setup.md` for installation and browser-login instructions, and
`src/docs/v1_build_guide.md` for the remaining implementation phases.

## Connect to the SQLite database

The agent uses a local SQLite file, so there is no database server, username, or password. The
default path is configured in `.env`:

```ini
DB_PATH=~/.jobsearchagent/jobsearch.db
```

After running the LinkedIn agent at least once, connect with the SQLite command-line client:

```bash
sqlite3 ~/.jobsearchagent/jobsearch.db
```

Inside the SQLite prompt, enable readable output and inspect the available tables:

```sql
.headers on
.mode column
.tables
.schema jobs
.schema review_queue
```

### List all tables

From inside the SQLite prompt:

```sql
.tables
```

Or directly from the terminal without opening the interactive prompt:

```bash
sqlite3 ~/.jobsearchagent/jobsearch.db ".tables"
```

### Describe all fields in a table

Use `PRAGMA table_info` to list every column, its type, nullability, default value, and primary-key
position:

```sql
PRAGMA table_info(jobs);
```

For the review queue:

```sql
PRAGMA table_info(review_queue);
```

To display the complete SQL statement used to create a table:

```sql
.schema jobs
```

From the terminal, the equivalent command is:

```bash
sqlite3 ~/.jobsearchagent/jobsearch.db "PRAGMA table_info(jobs);"
```

### Useful queries

List the latest discovered jobs:

```sql
SELECT discovered_at, title, company, location, outcome, fit_score, apply_status
FROM jobs
ORDER BY discovered_at DESC
LIMIT 20;
```

List jobs JEV accepted:

```sql
SELECT title, company, location, fit_score, apply_status, url
FROM jobs
WHERE outcome = 'accept'
ORDER BY fit_score DESC;
```

List jobs waiting for manual review:

```sql
SELECT created_at, reason, link
FROM review_queue
WHERE status = 'pending'
ORDER BY created_at DESC;
```

Count jobs by outcome:

```sql
SELECT COALESCE(outcome, 'not_triaged') AS result, COUNT(*) AS total
FROM jobs
GROUP BY outcome
ORDER BY total DESC;
```

Show one complete job, including its description:

```sql
SELECT *
FROM jobs
WHERE source_job_id = 'LINKEDIN_JOB_ID';
```

Exit the SQLite prompt with:

```sql
.quit
```

If `DB_PATH` has been changed in `.env`, pass that path to `sqlite3` instead.

## Delete stored jobs

To clear job data for a fresh discovery run while preserving the database file and its tables:

```bash
PYTHONPATH=src python scripts/delete_jobs.py
```

The script displays how many rows will be removed and requires typing `DELETE`. By default it
clears both `jobs` and `review_queue`, because review entries refer to stored jobs.

Preserve review-queue entries:

```bash
PYTHONPATH=src python scripts/delete_jobs.py --keep-review-queue
```

Skip the confirmation prompt, for example in a local test script:

```bash
PYTHONPATH=src python scripts/delete_jobs.py --yes
```

Use a database other than `DB_PATH`:

```bash
PYTHONPATH=src python scripts/delete_jobs.py \
  --db-path /path/to/jobsearch.db
```
