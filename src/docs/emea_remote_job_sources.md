# EMEA remote and project IT sources

Where to look for **remote IT** roles that are realistically open from Oslo (EEA), with an initial focus on **EMEA**, and a bias toward boards where **project / contract / remote collaboration** is a normal hiring mode — not only permanent “remote-first” jobs.

Profiles this list is aimed at: ServiceNow, full-stack / frontend, AI / agents. Markets already in this repo: Norway + truly remote, plus Spain/EU listings in `sites.yaml`.

## What `run` collects today

The status block in [`multisite_build_guide.md`](./multisite_build_guide.md) is the list of enabled rows. These are the adapters that save jobs:

| id | Board | How the run reads it |
| --- | --- | --- |
| `himalayas` | Himalayas developer jobs | One feed. Keeps Norway, Spain, or worldwide. |
| `eurotechjobs` | EuroTechJobs keyword search | One token per query (`ServiceNow developer` → `servicenow`). The default work type keeps a card only when it says remote. |
| `eurotechjobs-remote` | EuroTechJobs remote category | The category page once. |

## How to use a board the agent does not read yet

1. Open the board in a **normal** browser. Filter to remote, Europe / EMEA / EU timezone, and contract or freelance when the site has that toggle.
2. Drop roles that require UK-only right-to-work, US time zones only, or residence in a country you will not work from. “EMEA” on a US company often still means “UK or UAE office.”
3. For a job you would apply to: copy the URL and the full description into SQLite / the review queue (same as the LinkedIn mitigation loop).
4. Generate the CV with `python -m jobsearchagent.tailor_cv` when the row has `profile_id` and `raw_description`.
5. Apply on that site by hand with the PDF.

## The 15 boards

Ranked for this search: EMEA-reachable IT, then how often they carry **projects / contracts / freelance missions**, then signal-to-noise for ServiceNow / full-stack / AI. Himalayas and EuroTechJobs are collected by `run`. Other boards in this table may already be enabled and still skipped until their adapter exists.

| # | Board | URL | Why it is on the list | Typical work |
| --- | --- | --- | --- | --- |
| 1 | **freelancermap** | [freelancermap.com](https://www.freelancermap.com/) | Largest EMEA IT **project** board (DACH-heavy, EU-wide). ServiceNow, SAP, Java, integration, and consulting missions show up here more than on remote-FTE boards. | Project / freelance, often remote or hybrid |
| 2 | **Malt** | [malt.com](https://www.malt.com/) | Pan-European freelance marketplace (FR, ES, DE, BE, NL, …). Strong in IT, data, cybersecurity, and AI missions with real company clients. | Freelance missions |
| 3 | **Worksome** | [worksome.com](https://www.worksome.com/) / [worksome.no](https://www.worksome.no/) | Nordic contractor platform (Norway, Denmark, UK, Lithuania). Closest “project + remote collaboration” fit to living in Oslo. | Contract / freelance |
| 4 | **Arc.dev** | [arc.dev](https://arc.dev/) | Vetted remote developers; mix of freelance and full-time. Already in `sites.yaml`. More global than EMEA-native — filter to EU timezone / Europe. | Freelance + remote FTE |
| 5 | **Uplink** | [uplink.tech](https://uplink.tech/) | German IT freelancer network. High-signal projects; German helps, English roles exist. Complements freelancermap. | Freelance projects |
| 6 | **EU Remote Jobs** | [euremotejobs.com](https://euremotejobs.com/) | Hand-curated remote jobs on **European time zones** at companies with EU entities. Built to answer “can I apply from the EU?” | Mostly remote FTE; some contract |
| 7 | **Jobgether** | [jobgether.com](https://jobgether.com/) | Remote jobs explicitly filtered by **time zone and work authorization** for Europe. Cuts US-only “remote” noise. | Remote FTE (some contract) |
| 8 | **Welcome to the Jungle** (Otta) | [welcometothejungle.com](https://www.welcometothejungle.com/) | Curated mid/senior tech at UK and EU startups and scale-ups. Salary and company pages. Already in `sites.yaml`. | Mostly FTE remote/hybrid |
| 9 | **Landing.jobs** | [landing.jobs](https://landing.jobs/) | Iberia and Southern Europe tech, including remote, plus visa/relocation notes. Matches the Spain + EU market in this repo. | FTE; some remote |
| 10 | **Himalayas** | [himalayas.app](https://himalayas.app/) | Global remote board with unusually specific **country eligibility** filters. Use that, not a vague EMEA tag. | Remote FTE |
| 11 | **We Work Remotely** | [weworkremotely.com](https://weworkremotely.com/) | High-volume remote-only programming categories (full-stack / front / back). Filter Europe / EMEA. Already in `sites.yaml`. | Remote FTE; occasional contract |
| 12 | **Remotive** | [remotive.com](https://remotive.com/) | Curated fully-remote software jobs. Filter Europe. Lower volume, less junk. Already in `sites.yaml`. | Remote FTE |
| 13 | **Honeypot** | [honeypot.io](https://www.honeypot.io/) | Europe-focused developer hiring (DACH, NL, FR, remote). Companies approach you from a profile — closer to collaboration than mass apply. | FTE + some contract |
| 14 | **EuroTechJobs** | [eurotechjobs.com](https://www.eurotechjobs.com/) | EU tech board already wired in `sites.yaml`. Useful for keyword path searches (servicenow, react, python, fullstack). Drop non-EEA permit roles. | Mix of FTE and contract |
| 15 | **Hacker News: Who is hiring?** | [hnhiring.com](https://hnhiring.com/) / [news.ycombinator.com/jobs](https://news.ycombinator.com/jobs) | Monthly thread; many posts say **Remote (EU)** up front. Direct to the company, not a board overlay. Already in `sites.yaml` as HN Jobs. | Mix; often contract-friendly startups |

Start with **1–5** if the goal is project-based or freelance remote work. Use **6–12** for employed remote roles that are actually open in EMEA. Use **13–15** as a weekly supplement, not a daily firehose.

## Filters that matter on every board

- **Where you may live:** EU / EEA / Europe / “EU timezone,” not “remote (US)” or “worldwide except Europe.”
- **Right to work:** you are EEA-eligible from Oslo. Skip “must have UK visa” and “must be based in Spain” unless you want that constraint (Spain-resident remote is common on Iberian boards).
- **Engagement type:** contract, freelance, project, B2B, “remote collaboration,” consulting. Permanent-only boards will still be useful, but they are not the project market.
- **Stack:** ServiceNow / ITSM; React / TypeScript / Python / Node; LLM / RAG / agents. Single tokens match URL-based boards (`sites.yaml` notes this for EuroTechJobs).
- **Duplicates:** the same Greenhouse/Lever posting appears on WWR, Remotive, Himalayas, and Jobgether. Dedupe on the canonical company URL before it hits the queue.

## Second wave (not in the 15)

Worth a bookmark once the list above is a habit. They are either country-local, perm-heavy, or noisier.

| Board | URL | Notes |
| --- | --- | --- |
| WorkRemotely.eu | [workremotely.eu](https://workremotely.eu/) | EU-wide remote from company career pages; overlap with EU Remote Jobs |
| Tecnoempleo | [tecnoempleo.com](https://www.tecnoempleo.com/) | Spain IT; `en_remoto` filter. In `sites.yaml` |
| Manfred | [getmanfred.com](https://www.getmanfred.com/) | Spanish tech with salary and remote %. In `sites.yaml` |
| FINN.no | [finn.no/job](https://www.finn.no/job) | Oslo/Norway; many hybrid; some remote/contract |
| Arbeitnow | [arbeitnow.com](https://www.arbeitnow.com/) | Germany, English-friendly, visa flags; confirm location |
| YunoJuno | [yunojuno.com](https://www.yunojuno.com/) | UK contractors; right-to-work filter is strict |
| Indeed / InfoJobs | country sites | Aggregators; high volume, weak “true EMEA remote,” hostile to bots |

Skip as a primary source: Upwork/Fiverr (not EMEA-native, race-to-bottom), generic US remote boards without a Europe filter, and LinkedIn automation (see the restriction guide).

## Boards the agent does not read yet

Use the steps above for any board that is not in the collection table. Malt, the Spain-only boards, and LinkedIn stay out of `run`. LinkedIn automation is covered in the restriction guide.

## Related docs

- [Multi-site build guide](./multisite_build_guide.md) — which rows are enabled, and which adapter is next
- [LinkedIn restriction mitigations](./linkedin_restriction_mitigations.md) — keep Playwright off linkedin.com
- [README](../../README.md) — `run`, the review UI, and `tailor_cv`
- `src/config/sites.yaml` — the registry `run` reads. `sites_extended.yaml` is the backup and is not an input.
