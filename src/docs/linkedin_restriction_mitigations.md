# LinkedIn restriction mitigations

How to keep using this repo as a **local review and CV tool** while reducing the chance that LinkedIn restricts the account.

This is an operational reading of LinkedIn’s public [Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387) article (User Agreement §8.2). It is not legal advice. LinkedIn can still restrict an account that already ran automation.

**The only measure that actually avoids a LinkedIn ban is to stop Playwright from driving linkedin.com.** Rate limits, `--dry-run`, and “I apply by hand later” do not make the automation allowed.

## What LinkedIn forbids (this repo)

LinkedIn does not permit third-party software — including scripts, bots, and browser automation — that **scrapes**, **copies**, or **automates activity** on linkedin.com.

In this repo that means:

| Command / step | Talks to LinkedIn? | Status |
| --- | --- | --- |
| `python -m jobsearchagent run` | No, while `linkedin` is in `blocked.sites` and its row is `enabled: false`. It walks Himalayas and EuroTechJobs. | Keep for those boards |
| `python -m jobsearchagent discover` | Same as `run`: enabled sites only, and it refuses before opening linkedin.com. | Keep for those boards |
| `python -m jobsearchagent.linkedin_agent` | Yes | Stop |
| `python -m jobsearchagent.discovery.linkedin_smoke` | Yes | Stop |
| `python -m jobsearchagent.apply.agent` | Yes: Easy Apply | Stop |
| `tailor_cv` when SQLite already has `raw_description` | No | Keep |
| `tailor_cv --url …` if the description is missing | Yes: opens Chromium and scrapes the posting | Stop / paste the text instead |
| Local UI (`serve` + `frontend`) Rejected / Applied / `#jobs` | No | Keep |
| You apply in a normal browser with the generated PDF | Normal use | Keep |

`--dry-run` still fills Easy Apply and clicks Save. Not clicking **Submit** does not make `run` allowed.

## Do this

### 1. Stop LinkedIn automation

Do not run the commands that still open linkedin.com:

```bash
PYTHONPATH=src python -m jobsearchagent.linkedin_agent …
PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke …
PYTHONPATH=src python -m jobsearchagent.apply.agent …
```

`run` and `discover` refuse to start a LinkedIn search while `linkedin` is listed under `blocked.sites` and the LinkedIn row is `enabled: false`. Leave both in place. Removing only one of them is not enough.

Do not pass `--live`. Do not try to continue past a LinkedIn login checkpoint, CAPTCHA, or security challenge in the Playwright window.

### 2. Keep the local queue and CV flow

Those steps never touch LinkedIn:

```bash
# Terminal 1 — JSON API
PYTHONPATH=src python -m jobsearchagent serve

# Terminal 2 — UI
cd frontend
npm run dev
```

- **`#review`** — open the stored job link in your **normal** browser, then mark the row Applied or Rejected.
- **`#jobs`** — check that triage did not drop something you still want (this only reads SQLite).

Generate CVs from jobs that already have a stored description and `profile_id`:

```bash
PYTHONPATH=src python -m jobsearchagent.tailor_cv
```

If a row has no `raw_description`, do **not** let the agent scrape LinkedIn. Copy the posting text from a page you opened yourself, then generate the CV once that text is in the database (or skip that row).

### 3. Find jobs in LinkedIn’s own product

Use the LinkedIn website or app as a member:

- Job alerts and saved searches
- Saved jobs
- Opening `/jobs/view/…` yourself

That is how you use LinkedIn itself. New listings from Himalayas and EuroTechJobs come from `run` and `discover`, which do not open linkedin.com while the block stays in place.

### 4. Apply by hand

Use the PDF under `TAILORED_CV_DIR` (default `/Users/macnolo/Desktop/Trabajo`) and apply in a normal browser. That matches how this repo is already meant to be used after the queue is filled.

### 5. Use one real account, in a normal browser

Login stays manual. Credentials are not stored. For LinkedIn itself, use the same real account in Chrome/Safari/Firefox — not the Playwright profile — once you have stopped automation.

If LinkedIn shows a checkpoint, complete it yourself in that normal browser, or stop. Do not script around it.

## Do not do this

These are **not** mitigations. They are still the same prohibited class of tool, and some of them make a restriction more likely:

- Extra or throwaway LinkedIn accounts
- Hiding Playwright (stealth builds, fingerprint spoofing, “human” mouse movement)
- Proxies or other ways to mask the session
- Bypassing CAPTCHA, login, or rate limits
- Raising `LINKEDIN_DAILY_APPLY_LIMIT` or lowering delays to go faster
- Wiring `tailor_cv` into `run` / Easy Apply upload (not implemented, and it would add more automation)

`.env` values such as `LINKEDIN_ACTION_DELAY_MS` and `LINKEDIN_DAILY_APPLY_LIMIT` only reduce how hard a bot hits the site. They are **not** a ban shield.

## Safer daily loop

1. For Himalayas and EuroTechJobs, use `run` or `discover`. For LinkedIn, use the normal website: review alerts, open listings, save jobs you care about.
2. For jobs you want a tailored CV: make sure the description is already in SQLite, or paste it yourself.
3. Run `python -m jobsearchagent.tailor_cv` for pending `review_queue` rows that have `profile_id` and a stored description.
4. In the local UI: reject irrelevant rows; keep `#jobs` as a second pass.
5. Apply on LinkedIn by hand with the new PDF. Mark the row Applied.

Jobs already in SQLite from earlier `run` / `discover` sessions can stay. Using them locally does not send new automated traffic. It also does not erase that those sessions already happened.

## If the account was already used with Playwright

Stopping now is the useful step. LinkedIn may already have the earlier automated sessions on the account. There is no in-repo action that clears that history.

If LinkedIn restricts the account, use LinkedIn’s own help/appeal flow. Do not try to recover access with a second account or by changing the scraper.

## Related docs

- [Prohibited software and extensions (LinkedIn Help)](https://www.linkedin.com/help/linkedin/answer/a1341387)
- [README](../../README.md) — local UI, `tailor_cv`, SQLite
- [Tailored CV guide](./tailored_cv_guide.md)
- [Multi-site build guide](./multisite_build_guide.md) — enabled rows, and the LinkedIn block
- [EMEA remote and project IT sources](./emea_remote_job_sources.md)
