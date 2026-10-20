# JobSearchAgent

JobSearchAgent is a local, LinkedIn-focused job discovery and triage system. It uses a persistent
Playwright browser session, local Ollama models, JEV typed decisions, and SQLite persistence.

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

- Selects exactly one candidate work profile.
- Evaluates whether the configured locations permit applying.
- Requires the core stack and explicit minimum experience to match.
- Reduces fit for missing secondary skills without rejecting for those gaps alone.
- Rejects only confirmed hard blockers.
- Returns `accept`, `reject`, or `needs_confirmation` with a fit score.

### Artifact requirements

`jobsearchagent.triage.requirements`:

- Assigns `["cv", "cover_letter"]` to accepted LinkedIn Easy Apply jobs.
- Avoids preparing artifacts for rejected or external jobs.
- Performs no additional model call for the fixed v1 requirements.
- Provides independent JEV `noul` gates for optional future artifacts.

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

## Not implemented yet

The current production command does **not** apply for jobs. These build-guide stages remain:

- Generate a tailored CV and cover letter.
- Validate generated artifacts against profile facts and JEV quality thresholds.
- Open and answer the multi-step LinkedIn Easy Apply form.
- Upload the validated CV and use the cover letter when supported.
- Stop at Review in dry-run mode or submit once in live mode.
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
