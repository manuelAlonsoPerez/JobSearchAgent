# 🧪 Building JEV Decision Agents on Ollama — Developer Guide

> Companion to [`architechture_prompt.md`](./architechture_prompt.md). This guide shows how to build
> the **JEV / System One decision agents** that power triage, requirements, fit-scoring, form-answering,
> and validation in the **Job Search Agent** — running **fully locally on Ollama, no tokens**.

---

## 1. What a JEV agent *is* (and isn't)

**JEV (TypeSafe AI's "System One") is a *decision* model, not a chat model.** It does **not** write
prose, code, or explanations. You give it a `state` (text/context) plus named, **typed questions**,
and it returns **typed answers with probabilities** — in a single forward pass.

| Use JEV for… | Use a generative LLM for… |
| --- | --- |
| classify, route, yes/no, score against a rubric | writing the CV, cover letter, emails, free-text answers |
| triage `accept`/`reject`/`needs_confirmation` | explaining or summarising |
| "does this job require a cover letter?" | anything that produces sentences |
| scoring fit 0–N, judging artifact quality | the stateful browser apply session |

> 🧭 **Rule of thumb:** if the output is a **label, a boolean, or a number**, it's a JEV job. If the
> output is **words a human will read**, it's a generative-LLM job.

### Where JEV is used in the Job Search Agent

| Pipeline step | JEV question | Type |
| --- | --- | --- |
| **2 · Triage** | which outcome fits this job? | `choice` (accept/reject/needs_confirmation) |
| **2 · Fit score** | how well does the profile fit? | `score` |
| **3 · Requirements** | is a cover letter / email / blurb needed? | `noul` (one per artifact) |
| **4/5 · Form answers** | pick the right option for a structured field | `choice` / `noul` / `score` |
| **Validator** | is this artifact appropriate? | `score` / `noul` (+ a **deterministic** fact-check) |

---

## 2. Prerequisites & install

- **Ollama ≥ 0.35** (the `/v1/systemone` endpoint ships here).
- Pull a decision model:

```bash
# 9B, most accurate — good default
ollama pull nimble

# smaller / faster alternatives (Together AI)
ollama pull tev1          # 4B
ollama pull tev1:0.8b     # 0.8B, fastest
```

- Verify the endpoint is alive (no API key needed locally):

```bash
curl http://localhost:11434/v1/systemone -H 'Content-Type: application/json' -d '{
  "model": "nimble",
  "state": "I was charged twice, please refund.",
  "questions": { "refund": { "type": "noul", "instructions": "Does the user ask for a refund?" } }
}'
```

> 💻 **CPU note (your iMac):** a decision is **one forward pass** that emits ~1 token, so it stays
> fast on CPU even though generation is slow. Lean on JEV for every decision; save the slow
> generative models for writing.

---

## 3. The `/v1/systemone` API

### Request

```jsonc
{
  "model": "nimble",
  "state": "…context the decision is about…",   // string OR object, e.g. {"job": "…"}
  "questions": {                                  // one or many, answered in one pass
    "<name>": { "type": "choice|noul|score", "instructions": "…", "criteria": … }
  }
}
```

### The three question types

| Type | `criteria` | Returns |
| --- | --- | --- |
| `choice` | 2–26 named options `{id: description}` | best option + per-option `probabilities` + `confidence` |
| `noul` | *(none — just `instructions`)* | `probability` that the condition is **true** (0–1) |
| `score` | 2–10 ordered labels (low → high) | a probability-weighted average over levels `0…n-1` |

**`choice`**
```json
{ "type": "choice", "instructions": "Which outcome fits this job?",
  "criteria": { "accept": "Clear fit, apply", "reject": "Not a fit", "needs_confirmation": "Needs human check" } }
```

**`noul`**
```json
{ "type": "noul", "instructions": "Does this posting require a cover letter?" }
```

**`score`**
```json
{ "type": "score", "instructions": "How strong is the fit?",
  "criteria": ["Weak", "Partial", "Strong"] }
```

### Response (shape)

```json
{
  "model": "nimble",
  "answers": {
    "outcome": { "type": "choice", "choice": "accept",
                 "probabilities": { "accept": 0.91, "reject": 0.06, "needs_confirmation": 0.03 },
                 "confidence": 0.88 }
  },
  "usage": { "input_tokens": 174, "output_tokens": 1 }
}
```

> ⚠️ **Verify field names once.** The API is young; exact keys (`probability` vs `score`) can shift
> between model/version. Run one `curl` and print the response before wiring a parser.

---

## 4. A minimal, `.env`-driven Python client

Matches the architecture decision: **model names come from `.env`** so configs swap without code.

```ini
# .env
JEV_BASE_URL=http://localhost:11434
JEV_MODEL=nimble          # swap to tev1 / tev1:0.8b to A/B
```

```python
# jev.py — tiny decision client (httpx, no SDK needed)
from __future__ import annotations
import os
import httpx

JEV_BASE_URL = os.environ.get("JEV_BASE_URL", "http://localhost:11434")
JEV_MODEL    = os.environ.get("JEV_MODEL", "nimble")

class Jev:
    def __init__(self, base_url: str = JEV_BASE_URL, model: str = JEV_MODEL, timeout: float = 60.0):
        self._url = f"{base_url.rstrip('/')}/v1/systemone"
        self._model = model
        self._client = httpx.Client(timeout=timeout)

    def ask(self, state, questions: dict) -> dict:
        """Send one or more typed questions; return the `answers` dict."""
        r = self._client.post(self._url, json={"model": self._model, "state": state, "questions": questions})
        r.raise_for_status()
        return r.json().get("answers", {})

    # --- convenience wrappers -------------------------------------------------
    def choice(self, state, instructions: str, options: dict[str, str]) -> tuple[str, float]:
        a = self.ask(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}})["q"]
        return a["choice"], a.get("confidence", max(a.get("probabilities", {}).values(), default=0.0))

    def noul(self, state, instructions: str) -> float:
        a = self.ask(state, {"q": {"type": "noul", "instructions": instructions}})["q"]
        return float(a.get("probability", a.get("true", 0.0)))   # verify key once

    def score(self, state, instructions: str, levels: list[str]) -> float:
        a = self.ask(state, {"q": {"type": "score", "instructions": instructions, "criteria": levels}})["q"]
        return float(a.get("score", a.get("value", 0.0)))         # verify key once
```

---

## 5. Decision patterns for the Job Search Agent

### 5.1 Triage + fit in **one** call (step 2)

Batch questions — the model answers them together, one pass:

```python
jev = Jev()
answers = jev.ask(
    state={"title": job["title"], "description": job["raw_description"],
           "profile": profile_summary},        # ground the decision well
    questions={
        "outcome": {"type": "choice", "instructions": "Pick the outcome for this candidate.",
                    "criteria": {"accept": "Required skills matched, no blocking constraint",
                                 "reject": "A required skill is missing or a hard mismatch",
                                 "needs_confirmation": "Needs a CEFR grade, Spain residence, or English score"}},
        "fit":     {"type": "score", "instructions": "How well does the profile fit?",
                    "criteria": ["Weak", "Partial", "Strong"]},
    },
)
outcome = answers["outcome"]["choice"]
fit     = answers["fit"].get("score")
```

> The `needs_confirmation` option maps straight to the **review queue** — never guessed.

### 5.2 Requirements — one `noul` per artifact (step 3)

```python
ARTIFACTS = {
    "cover_letter": "Does this posting ask for a cover / motivation letter?",
    "email":        "Is the only way to apply an email address (no form)?",
    "project_blurb":"Does it ask for a portfolio or project description?",
}
required = [name for name, q in ARTIFACTS.items() if jev.noul(job_state, q) >= 0.5]
# CV is always produced; these are the conditional extras
```

### 5.3 Form-field answering (steps 4 & 5)

```python
def answer_field(field, profile):
    if field.type in ("select", "radio"):
        choice, conf = jev.choice(profile, f"Best option for: {field.label}",
                                  {opt: opt for opt in field.options})
        return FieldAnswer(field_id=field.id, value=choice, source="jev", confidence=conf)
    if field.type == "checkbox":
        p = jev.noul(profile, f"Should '{field.label}' be checked for this candidate?")
        return FieldAnswer(field_id=field.id, value=(p >= 0.5), source="jev", confidence=p)
    # free text → hand to the generative writer, not JEV
    return None
```

> Fields asking for **CEFR grade / Spain residence / English score** → **don't call JEV** → emit a
> `deferred` answer and route the job to the review queue (see `match_rules` in `work_profiles.yaml`).

### 5.4 Validator — JEV judges quality, **rules** guard truth

```python
def validate(artifact_text: str, profile_facts: str) -> bool:
    # (a) MUST-PASS deterministic truthfulness check (not JEV):
    if claims_unlisted_skill(artifact_text, profile_facts):   # your own cross-check
        return False
    # (b) JEV appropriateness, thresholded:
    quality = jev.score(artifact_text, "How appropriate is this for the job and profile?",
                        ["Poor", "Acceptable", "Strong"])
    return quality >= QUALITY_THRESHOLD
```

> 🔐 **Never** let JEV be the *sole* guard on truthfulness — local decision models aren't calibrated
> enough. Deterministic fact-check first (hard gate), JEV for the "is it good?" judgment.

---

## 6. Thresholds & calibration

Local models are strong at **discrimination** but **less calibrated** than hosted Jev, so a raw
`0.5` isn't gospel. Before trusting a threshold unattended:

1. Collect **~20–50 labelled examples** per decision (real job postings you've hand-judged).
2. Run them through the question, **log the probabilities**, and pick the threshold that hits your
   precision/recall target (bias toward *review-queue* over wrong auto-apply).
3. Store thresholds in config (e.g. `jev_thresholds.yaml`) so they're tunable without code.
4. Re-check when you switch `JEV_MODEL` (`nimble` ↔ `tev1`) — thresholds don't transfer.

---

## 7. Using JEV inside a LangGraph node

```python
def triage_node(state: JobState) -> dict:
    a = jev.ask(state=_state_for(state), questions=TRIAGE_QUESTIONS)
    outcome = a["outcome"]["choice"]
    return {
        "outcome": outcome,
        "fit_score": a["fit"].get("score"),
        "review_reason": "needs_confirmation" if outcome == "needs_confirmation" else None,
    }
# conditional edge then routes: accept → requirements, reject → log, else → review_queue
```

Keep JEV calls **pure and deterministic-ish**: same `state` + `questions` → stable decision. That
makes nodes easy to checkpoint, test, and replay.

---

## 8. Performance & ops on CPU

- **One forward pass per request** → sub-second to a few seconds on CPU; batch multiple questions in
  one call to amortise.
- **Model pick:** `nimble` (9B) for accuracy; `tev1` (4B) / `tev1:0.8b` when you need speed or the
  question is simple. Tiny models degrade on nuanced `noul`/`score` — test before trusting.
- **Health check:** add an `agent doctor`-style command that pings `/v1/systemone` with a known
  question and asserts a sane answer, so overnight runs fail loudly if Ollama is down.

---

## 9. Pitfalls & principles

- ✅ **Ground the `state`.** Pass the job text + a profile summary; a vague state gives a vague
  probability.
- ✅ **Keep `choice` options mutually exclusive & exhaustive**, each with a clear description.
- ✅ **Batch questions** that share context into one request.
- ❌ **Don't ask JEV to generate** anything — it won't, and shouldn't.
- ❌ **Don't hardcode thresholds** — config them and calibrate.
- ❌ **Don't trust tiny models** on subtle judgments without a labelled check.

---

## 10. Quick reference

| Thing | Value |
| --- | --- |
| Endpoint | `POST {JEV_BASE_URL}/v1/systemone` |
| Env | `JEV_BASE_URL`, `JEV_MODEL` |
| Models | `nimble` (9B) · `tev1` (4B) · `tev1:0.8b` |
| Question types | `choice` (2–26 options) · `noul` (→ P(true)) · `score` (2–10 ordered levels) |
| Returns | typed answers + probabilities, `usage` |
| Auth | none (local) |
| Rule | labels/booleans/numbers → JEV; sentences → generative LLM |

> Next: wire these into the `triage`, `requirements`, `form-answering`, and `validator` nodes from
> [`architechture_prompt.md`](./architechture_prompt.md), reading `JEV_MODEL` from `.env`.
