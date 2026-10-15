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

Ollama is a local application rather than a Python dependency. Install it from
[ollama.com/download](https://ollama.com/download), start it, and pull the configured models:

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
