# Tailored CV agent — developing guide

Standalone agent. Not wired into `run` / Easy Apply yet. You will test it on its own.

A pending review row that already has `raw_description` and `profile_id` is tailored from SQLite. That includes jobs `run` saved from RemoteOK, Himalayas, or EuroTechJobs. Passing `--url` scrapes LinkedIn only when that text is missing, and that scrape stays refused while LinkedIn is blocked.

Custom, local, free. No Canva MCP, no LightPDF. Duplicate the two Canva layouts in HTML once; every tailored file is a new PDF.

Say *"build Part T1"* (then T2…) and implement only that part.

## Goal (this phase)

Given a **chosen work profile** and a **LinkedIn job URL**:

1. Find the matching source CV in `src/docs` from that profile’s `source_cvs`.
2. Write a new CV tailored to the offer (same look, facts only from the source CV and yaml).
3. Save it as a **new file** whose name includes the job (title / company / LinkedIn id).

```
--profile + --url
        │
        ├─ load work_profiles.yaml
        ├─ resolve first existing PDF in src/docs from source_cvs
        ├─ scrape LinkedIn job (existing discovery, login in the persistent Chrome profile)
        ├─ extract CV text
        ├─ Ollama rewrite toward the offer (WRITER_MODEL)
        ├─ fact-check
        └─ HTML template → Playwright PDF → disk
```

**Out of scope for now:** graph / `write_cv` hook, cover letters, inventing skills, Canva API, batch `run`.

## Inputs

| Flag / input | Meaning |
| --- | --- |
| `--profile` | Work-profile id from `work_profiles.yaml`: `servicenow-developer`, `fullstack-developer`, `frontend-developer`, `ai-developer` |
| `--url` | LinkedIn `/jobs/view/…` URL |

The source PDF is **not** passed by path. Resolve it with the existing `resolve_cv_path` / `source_cvs` list against `src/docs` (same matching as Easy Apply).

| Data | Where |
| --- | --- |
| Source CVs | `src/docs/Manuel Alonso - Developer ServiceNow.pdf`, `src/docs/Manuel Alonso - Developer Full stack.pdf` |
| Profile facts | `src/config/work_profiles.yaml` (`source_cvs`, `not_claimed`, skills, dates) |
| Job | scraped `title`, `company`, `source_job_id`, `raw_description` |
| Writer | `llm.chat(..., model="writer")` |
| Render | Playwright HTML → PDF (extend `utils/pdf.py`) |

## Output filename

Never overwrite the source PDF. Write a **new** file, job in the name:

```
/Users/macnolo/Desktop/Trabajo/{job-id} - {company} - {job-title}.pdf
```

Sanitize `/` and other path characters in company and title. Keep `job-id` from the LinkedIn URL so two roles at the same company do not collide. Print the absolute path when done.

## Hard rules

- No fact that is not in the source CV or `work_profiles.yaml`.
- Honour `not_claimed` and unfinished education notes (e.g. the MSc).
- Do not stretch years or rename titles.
- Prefer dropping an irrelevant bullet over inventing a relevant one.

## How to test (standalone)

After T5:

```bash
PYTHONPATH=src python -m jobsearchagent.tailor_cv \
  --profile servicenow-developer \
  --url https://www.linkedin.com/jobs/view/1234567890/
```

Expect: the ServiceNow PDF from `src/docs` is used, a new file is written under `/Users/macnolo/Desktop/Trabajo/` with that job in the filename, and the terminal prints the path. Repeat with `--profile fullstack-developer` and a React/full-stack URL. Does not open Easy Apply and does not Submit.

## Parts

### ☑ T1 — Resolve profile → source CV, extract text · `CODE`

- Load `--profile` via `load_profile`. Unknown id fails with the available ids.
- Find the CV with `resolve_cv_path` / `source_cvs` in `src/docs`. Missing file fails clearly.
- Extract plain text (pypdf or pdftotext; local only).
- Return `template_id` (`servicenow` \| `fullstack` from the filename, not the LLM), `path`, `raw_text`.

**Done when:** `servicenow-developer` resolves to the ServiceNow PDF and extracts non-empty text; a fake profile id errors.

### ☑ T2 — Two HTML templates · `CODE`

- One A4 template per Canva CV. Clone layout by eye from the two PDFs. CSS in `src/jobsearchagent/preparation/templates/`.
- Templates take **structured fields only** (name, contacts, summary, jobs, skills).
- `render_cv_pdf(template_id, fields, output_path)` via Playwright.

**Done when:** filling a template with the *unmodified* extracted CV produces a PDF that is recognisably the same layout as the source.

### ☑ T3 — Rewrite toward the job · `GEN`

- Scrape `--url` with existing LinkedIn discovery (persistent login).
- Prompt: job title + description + extracted CV + profile facts + `not_claimed`.
- Model may rewrite summary, reorder jobs, emphasise matching bullets, trim noise.
- Model may not add employers, degrees, tools, or numbers that are not in the source.
- Return JSON matching the template fields.

**Done when:** a ServiceNow offer on `--profile servicenow-developer` emphasises ITSM/Flow Designer and does not invent certs.

### ☑ T4 — Validate · `CODE`

- Every skill/employer/date in the JSON exists in the source CV or yaml; nothing from `not_claimed`.
- Fail → retry T3 up to `MAX_ARTIFACT_ATTEMPTS`, else exit non-zero (no graph `artifact.status` yet).

**Done when:** planting “Kubernetes lead, 8 years” in a fixture fails.

### ☑ T5 — Standalone module · `CODE`

- New module, not a `run` subcommand: `python -m jobsearchagent.tailor_cv --profile … --url …`
- Runs T1–T4, writes the filename above, prints the path.
- No Easy Apply, no SQLite job graph, no Submit.

**Done when:** one real LinkedIn URL + each of the two main profiles produces a distinct tailored PDF you can open and check.

## Suggested order

T1 → T2 (unmodified fill) → T3 → T4 → T5.

Do not call the LLM until T2 can reprint the source CV. Do not hook `preparation/cv.py` until this standalone agent is good enough to keep.

## Later (not this phase)

Wire the same functions into A4 `write_cv` so `run` can attach a tailored PDF. Cover letter can then read that file.

## Not this

- Canva MCP / LightPDF / any paid rewrite API.
- Uploading the source PDF to a remote editor.
- Overwriting `src/docs`.
