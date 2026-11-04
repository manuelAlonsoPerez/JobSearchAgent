# Multi-site discovery — planning prompt

> **Role:** Act as my architecture partner. **Don't jump to code.** Help me converge on site selection, a discovery interface, and how `run` walks sites — then we implement incrementally, in the same part-by-part style as `src/docs/v1_build_guide.md`.

---

## Goal

Extend JobSearchAgent so **`python -m jobsearchagent run` is no longer LinkedIn-only**.

The same command should:

```bash
PYTHONPATH=src python -m jobsearchagent run --dry-run --limit 15 --max-jobs 80 --month 1
```

1. Read enabled sites from `src/config/sites.yaml`
2. Go **site by site** (then query × location where the site supports it)
3. Discover jobs, normalize them, dedupe into SQLite
4. Run the existing G1 graph (triage → requirements → apply/record)

Keep the current UX: `--limit` = new jobs per search unit, `--max-jobs` = run total, `--month N` = recency, `--dry-run` = never submit. LinkedIn Easy Apply fill stays as it is. Other sites must still produce `JobState` rows and review-queue items.

**Autonomy first:** the overnight batch still runs unattended. Login challenges, CAPTCHAs, and unknown apply UIs stop that job and queue it — they must not block the rest of the run.

---

## Candidate and markets (locked)

- Lives in **Oslo**, **EEA-eligible**. Target: **IT roles** that are **remote**, **project/contract**, or **EMEA**.
- Profiles in `work_profiles.yaml`: ServiceNow, full-stack (React/TS/Python/Node/Java/.NET), AI/RAG/agents.
- Location eligibility already exists in JEV triage (`JOB_LOCATIONS`, EEA/Europe/Nordic/worldwide remote). Discovery can be broader; triage still rejects geo-locked remote that excludes Norway.
- Spanish “100% remoto but Spain-resident” ads: keep in the registry only if we explicitly want them; otherwise drop Spain-only boards from the **enabled** set.

---

## What already exists (reuse, don't reinvent)

| Piece                                           | Today                                                                                                                                                                                                         |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/config/sites.yaml`                         | Registry: `sites[]` with `name`, `url`, `type: search \| static`, plus `manual_ats`, `blocked`, `blocked_sso`, `base_urls`. Placeholders `{query_encoded}` / `{location_encoded}`. **Not iterated by `run`.** |
| `src/config/searches.yaml`                      | Tiered queries. CLI uses these for LinkedIn only.                                                                                                                                                             |
| `config.load_sites()` / `linkedin_search_url()` | YAML is loaded, but URL building is hardcoded to the `"LinkedIn Jobs"` entry.                                                                                                                                 |
| `cli.discover_jobs` / `run_batch`               | One browser session → `LinkedInDiscovery` → query × location. Prints “N LinkedIn searches”.                                                                                                                   |
| `discovery/linkedin.py`                         | Authenticated Playwright scrape, Easy Apply detection, LinkedIn filters (`f_TPR`, `f_WT`, `geoId`, Easy Apply).                                                                                               |
| `JobState`                                      | Already has `site`, `source_job_id`, `url`, `easy_apply`, `apply_url`.                                                                                                                                        |
| Dedup                                           | `(site, source_job_id)` then canonical URL (`database.seen`, `utils.text`).                                                                                                                                   |
| G1 graph                                        | `thread_id = linkedin:{source_job_id}`. Non-Easy-Apply → `external` + review queue.                                                                                                                           |
| Apply                                           | LinkedIn Easy Apply fill / save / dry-run only.                                                                                                                                                               |
| v1 build guide                                  | Explicitly deferred: “Workday/`employers.yaml`, smart-extract, external ATS automation, **multi-site**.”                                                                                                      |

Architecture (`src/docs/architechture_prompt.md`) already said discovery should iterate `sites.yaml`. LinkedIn was the MVP override. This phase is that deferred work — **discovery-first**, not a rewrite of triage or Easy Apply.

---

## Hard constraints

- Local, Ollama, CPU-only, overnight batch. No cloud scrape APIs unless we explicitly choose one.
- YAML is the source of truth for which sites run. Agents/code **read** `sites.yaml`; site lists are not hardcoded in Python.
- Respect `blocked`, `blocked_sso`, `manual_ats`. Indeed/Glassdoor/Google/Accenture stay out unless we reopen that.
- Truthfulness and JEV triage stay as they are. Multi-site must not weaken location/skills decisions.
- Persistent Playwright profile; first login per site is **manual**. No credential storage, no CAPTCHA bypass, no ToS-evasion.
- Dedup must work **across sites** when the same posting appears on LinkedIn and a board (URL and/or company+title+location — decide together).
- `--dry-run` remains the default. Never submit on a new site in this phase.
- Do not automate apply on arbitrary ATS in v2. Unknown apply UI → `external` / `manual` + review queue with the real apply link.
- Keep LinkedIn working. A multi-site refactor that breaks `run` on LinkedIn is a failed design.

---

## Site-selection brief (this is a first-class planning task)

`sites.yaml` is a large mixed list (Spain boards, aggregators, remote-first, HN). **Curate an enabled set** for IT + remote / project-based / EMEA. Everything else stays in YAML as `enabled: false` (or equivalent) so we can turn it on later.

Prefer boards that are:

1. **Remote-first developer jobs** (RemoteOK, We Work Remotely, Remotive, Himalayas, JustRemote, Working Nomads, Nodesk, Dynamite Jobs, Jobgether, Startup.jobs)
2. **EMEA / EU tech** (EU Remote Jobs, EuroTechJobs, Landing.jobs, Welcome to the Jungle)
3. **Contract / project / freelance IT** (Arc.dev and similar EU-friendly contract boards — propose extras such as Malt, FreelancerMap, Twago only if they fit scrape + apply ethics)
4. **LinkedIn** (keep; it already works)

Treat as **out of v2 enabled set** unless we argue them in:

- Spain-only product boards if we are not targeting Spain residence (InfoJobs, Tecnoempleo, Ticjob, Manfred, Michael Page ES) — they conflict with the Oslo/EMEA brief
- Bot-hostile aggregators (Indeed, Talent.com) already noted as 403 / browser-only
- Get on Board if it is mostly LATAM
- Hacker News Jobs if volume/signal is too low vs scrape cost

For each **enabled** site, the plan must state:

- `search` vs `static` (and whether `{query_encoded}` / `{location_encoded}` actually work)
- Auth: none / cookie / manual login
- How recency maps to `--month` (native filter vs post-filter on listing date)
- How remote / EMEA / contract is expressed (URL, checkbox, or post-filter)
- Listing → job-detail strategy (same page vs click-through)
- Apply model: native form vs external ATS vs “queue the link”
- Anti-bot risk and whether this site shares the LinkedIn Chrome profile or needs its own

---

## Design questions to settle before code

1. **Search unit.** Today: query × location on one site. Tomorrow: site × query × location? Static category pages once per site? How do `--limit` and `--max-jobs` apply so a noisy board cannot eat the whole budget?
2. **Adapter shape.** Shared protocol (`discover(page, query, location, limit, filters) -> list[JobState]`) vs one mega-scraper vs HTTP/Jina for static boards and Playwright only when needed.
3. **`sites.yaml` schema.** Minimum new fields: `enabled`, `id`, `adapter`, `auth`, `supports_query`, `supports_location`, `posted_within`, `work_type`, `profile` (shared vs dedicated). Keep it small.
4. **CLI.** Same flags for all sites, or `--site` / `--skip-site`? LinkedIn-only flags (`--geo-id`, `--easy-apply`) should no-op or skip on sites that lack them.
5. **Filters.** `--month`, `--work-type remote` are LinkedIn query params today. Per-site native filter vs scrape-then-filter vs “unsupported → skip site”.
6. **Apply boundary.** Confirm: v2 auto-fill = LinkedIn Easy Apply only; every other accept goes to `review_queue` with `apply_url`. When does a second apply adapter get its own later part?
7. **Browser sessions.** One Chromium profile for all logins vs `chrome-profile/<site_id>`. Rate limits / daily caps per site, not only LinkedIn.
8. **Graph / persistence.** `thread_id = {site_id}:{source_job_id}`. Cross-site duplicates. `easy_apply` meaning on non-LinkedIn jobs.
9. **Queries.** Same `searches.yaml` phrases everywhere, or per-site query maps (EuroTechJobs wants `servicenow`, InfoJobs wanted hyphens)?
10. **Order of work.** First vertical after LinkedIn: one static remote board or one search-shaped EU board? Build the interface around the second site, not a 20-site bang.

---

## Proposed working hypothesis (challenge this)

```text
for site in enabled(sites.yaml):     # skip blocked
  for query in searches.yaml:        # skip if site is static/category
    for location in locations:       # skip if site has no location param
      discover → normalize JobState(site=...)
      stop at --limit / --max-jobs
→ existing G1 per job
    LinkedIn Easy Apply → dry-run fill / save / queue
    else → external/manual + review_queue
```

Triage, profiles, SQLite, review UI stay shared. New work lives in **discovery + config + CLI loop**. Apply stays LinkedIn until a later part.

---

## Deliverable for the planning session

Do **not** write implementation code yet. Produce:

1. **Enabled site shortlist** from `sites.yaml` (+ at most a few new EMEA/contract boards), each with one-line why / why-not
2. **Target `sites.yaml` schema** (fields we will actually add)
3. **Discovery interface** and how LinkedIn becomes one adapter
4. **How `run` changes** (loop, flags, limits, `--month`) without breaking the command above
5. **Apply policy** for non-LinkedIn jobs in this phase
6. **Part-by-part build order** in v1-guide style (e.g. M1 schema, M2 protocol, M3 second site, M4 CLI loop, M5 remaining enabled sites)
7. **Trade-offs** (shared vs per-site browser, native filters vs post-filter, query rewriting)

Propose, then let me steer. Once we lock this, we append it as a build guide section and implement Part M1 only.

---

## Success criteria (when we later build)

- The command `PYTHONPATH=src python -m jobsearchagent run --dry-run --limit 15 --max-jobs 80 --month 1` walks **enabled sites**, not only LinkedIn
- LinkedIn behavior is unchanged when it is the only enabled site
- Jobs from other sites appear in SQLite with the right `site`, survive dedup, get JEV triage, and land in review when they are not Easy Apply
- A site failure (login wall, empty listing, 403) logs, queues if needed, and **continues to the next site**
- New sites are added by YAML + a small adapter, not by forking `cli.py`

---
