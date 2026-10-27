# JobSearchAgent development setup

## 1. Database requirement

The MVP uses **SQLite**, which is embedded in Python. You do not need to install or run a database
server, create a database account, or configure a port.

Verify that your Python installation includes SQLite:

```bash
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Any version number means it is available. This machine currently reports SQLite `3.43.2`.
Part F3 will create the database file configured by `DB_PATH` when the application first runs.
The separate `sqlite3` command-line program is optional and is not required by JobSearchAgent.

## 2. Python environment

Use Python 3.11 or newer. From the repository root:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate `.venv` again whenever you open a new terminal for this project.

## 3. Playwright browser runtime

The Python package is installed by `requirements.txt`. Install its Chromium runtime separately:

```bash
python -m playwright install chromium
```

LinkedIn login will be completed manually later, when browser part F4c is implemented. Do not put
LinkedIn credentials in `.env`.

## 4. Environment configuration

Create the local environment file if it does not already exist:

```bash
cp .env.example .env
```

Keep `DRY_RUN=true` while developing. The `.env` file is ignored by Git; `.env.example` contains
the safe defaults that are committed.

The default database and browser-profile locations are:

```text
~/.jobsearchagent/jobsearch.db
~/.jobsearchagent/chrome-profile
```

Part F3 will create the parent directory and database file. No manual database initialization is
needed.

## 5. Ollama

Ollama is a local application rather than a Python dependency. Install version `0.40.4` or newer
from [ollama.com/download](https://ollama.com/download), start it, and pull the configured models:

```bash
ollama pull nimble
ollama pull qwen2.5:7b
```

Verify that the local service is available:

```bash
curl http://localhost:11434/api/tags
```

Ollama is not required for F3 database development, but it will be needed for F4a and F4b.

## 6. Verify the current foundation

With `.venv` active:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The F1/F2 configuration and model tests should pass before starting F3.

## 7. Run the interactive LinkedIn scraper smoke test

First ensure Ollama is running, `nimble` is installed, and the virtual environment is active:

```bash
ollama --version
ollama pull nimble
source .venv/bin/activate
```

From the repository root, run:

```bash
PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke \
  --query ServiceNow \
  --max-jobs 3
```

The command reads search and acceptance locations from `.env`:

```ini
JOB_LOCATIONS=["Norway", "Remote"]
```

It searches each configured location. For JEV routing, a job is eligible when it is based in
Norway, explicitly permits remote work from Norway, includes Norway through a wider scope such as
EEA/Europe/Nordic, or is genuinely worldwide/unrestricted remote. A restricted remote job that
excludes Norway is rejected; an ambiguous remote scope requires confirmation.

For a one-off override, repeat `--location` as needed:

```bash
PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke \
  --query React \
  --location Norway \
  --location Remote \
  --max-jobs 3
```

The command opens the persistent Chromium profile. Complete LinkedIn login manually when requested,
then return to the terminal and press Enter. The test searches for remote Easy Apply jobs from the
past week, ordered by relevance. It prints the extracted offer and JEV's typed profile, location,
outcome, and fit result for each job, then asks whether to analyze the next result.

On CPU-only hardware, the single batched JEV triage call can take several minutes.
`JEV_TIMEOUT_SECONDS=500` in `.env` controls its timeout.

This smoke test does **not** submit applications. LinkedIn credentials are entered only in the
browser and are not stored in `.env` or read by the script.

This exercises the current discovery-and-triage MVP slice. CV generation, form filling, and
submission are later build-guide phases and are not part of this command yet.

## 8. Run the production LinkedIn discovery-and-triage agent

The production entry point reuses the smoke-tested scraper and JEV policy, deduplicates jobs in
SQLite, persists each result, and sends external applications, uncertain decisions, and failures
to the non-blocking review queue:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query React \
  --max-jobs 5
```

It reads locations from `JOB_LOCATIONS`. Repeat `--location` to override them for one run:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent \
  --query ServiceNow \
  --location Norway \
  --location Remote \
  --max-jobs 5
```

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent --query ServiceNow --geo-id 91000007 --posted-within week --work-type remote --easy-apply --sort relevance --max-jobs 15
```

Accepted Easy Apply jobs from the discovery agent are stored as `dry_run_ready`.
To fill one modal and record the result (still no Submit while `DRY_RUN=true`):

```bash
# Uses src/jobsearchagent/apply/decision.json and the profile CV in src/docs
PYTHONPATH=src python -m jobsearchagent.apply.agent
```

Pass `--live` only when you want one real submission. `smoke_apply` remains the
inspect-only runner that waits on Review and does not write the database.

## 9. Run the batch CLI (discover → apply graph)

`python -m jobsearchagent run` discovers LinkedIn jobs and walks each one through
triage, Easy Apply fill, and record. `--dry-run` never clicks Submit:

```bash
PYTHONPATH=src python -m jobsearchagent run --query "ServiceNow developer" --geo-id 91000007 --work-type remote --dry-run
```

`--geo-id 91000007` is EMEA and replaces `--location` for the search. Add
`--work-type remote` (the default), `--posted-within week`, or `--sort recent`
to match a LinkedIn-agent search.

`--easy-apply-only` is the default. Use `--include-external` to also record jobs
that open an external apply URL. `--limit` caps new jobs. The command prints
counts for discovered, duplicate, rejected, review, external, dry-run-ready, and
applied.

Search only, without opening Easy Apply:

```bash
PYTHONPATH=src python -m jobsearchagent discover \
  --query "ServiceNow developer" \
  --location Spain
```
