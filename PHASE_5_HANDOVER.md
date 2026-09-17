# Phase 5 Handover — Session 7 (deployment & productionization)

**Status:** Phases 1–4 complete and committed/published —
https://github.com/amaan-j12/rag-due-diligence-copilot (+ case study +
PHASE_4_HANDOVER.md). This document scopes **Phase 5: production deployment and
the remaining "real deployment" tail** that BUILD_SPEC Part 9 scoped as
"Phase 4 step 18" (deploy final Haiku-API version, log <$5 spend) and
`NEXT_PHASE_CHECKLIST.md` scopes as the Docker/GCP/HF-Spaces deployment checklist.

Phase 5 is **optional** for the portfolio but is the natural next step if you want
a *live demo link* (vs. the local-demo / screenshots story Phase 4 shipped with).

---

## What Phase 4 actually left behind

Phase 4 is done in code and on GitHub. The concrete loose ends (all small):

| Item | State | Effort |
|---|---|---|
| `.github/workflows/eval-gate.yml` final push | committed locally (`f7225db`), NOT on GitHub — blocked on `workflow` token scope until `gh auth refresh -s workflow` + browser approve + `git push` | 2 min |
| Live hosted demo (Streamlit Cloud / HF Spaces / Docker) | not started — Phase 4 shipped local-demo + screenshots | 2–6 h |
| One real Haiku-API eval run to log actual $ | not started (optional, strengthens cost claim) | 1–2 h |
| Langfuse (or paid tracer) wiring | not needed — `src/observability/trace.py` already writes Langfuse-shaped JSONL; optional if you want a hosted trace UI | 2–3 h |

---

## Phase 5 goals (pick in order of portfolio value)

### 5.1 Finish the publishing tail (required, 15 min)
1. `gh auth refresh -h github.com -s workflow` → browser-approve `workflow` scope.
2. `git push -u origin main` — uploads commit `f7225db` containing
   `.github/workflows/eval-gate.yml`.
3. Confirm the Actions tab shows the `LLMOps eval gate` workflow.

### 5.2 Live hosted demo (recommended, 2–6 h)
Goal: a URL an interviewer can click. Two viable paths:

- **Streamlit Community Cloud (cheapest, most presentable):** create the repo's
  Streamlit deployment. Constraint: the app loads SentenceTransformer +
  CrossEncoder + a 39M chunks.jsonl + local Qdrant/BM25 — all fine in the container
  image, but heavy for a free CPU tier. Bottlenecks: model download (~1.5 GB) at
  container startup and per-query embedding/rerank latency. See
  `NEXT_PHASE_CHECKLIST.md` for the full Docker/GCP path and HF-Spaces notes
  (it also calls out: HF free CPU tier may be too slow — test deploy **early**).
- **Docker + managed VM (GCP/AWS free tier):** full pipeline in a container; most
  robust but several hours of infra work. Full runbook in `NEXT_PHASE_CHECKLIST.md`.

**Decision driver:** interviewer value = seeing the RBAC role selector +
faithfulness badge live vs. reading it in a case study. If > 3 h, keep the local
demo + `streamlit run app.py` quickstart and don't publish a slow link.

### 5.3 Log real API spend on a Haiku run (optional, 1–2 h)
Do one RAGAS/verifier pass through the actual Anthropic Haiku API (they exist as a
fallback already — force it by pointing `_try_opencode_cli`/`_try_claude_cli` to
fail, or call `_try_anthropic_api` directly) over the CI subset or 30-question set.
Log the real `$` into `logs/api_usage.jsonl` and update the cost section of
`docs/CASE_STUDY.md`. Expected < $1 at Haiku pricing (Phase 4 estimate
$0.014/query × 30 ≈ $0.42).

### 5.4 (Deferred, low value) Langfuse trace UI
Not required. `src/observability/trace.py` output is already Langfuse-compatible
JSONL; if you later want a hosted UI, add the `langfuse` SDK and forward
`logs/traces.jsonl` records. Do this only after 5.1–5.3.

---

## Known limitations to mention in the interview (and optionally fix in Phase 5)

1. **RAGAS scores incomplete** — `context_precision`/`context_recall` are NaN for
   hybrid/rerank in the committed summaries (missing hand-labeled ground truth on
   those runs). Faithfulness/relevancy are sound. Fix: label the 30 eval questions
   with the expected chunk IDs and re-run `eval/run_ragas.py`.
2. **Naive `context_recall = 0.0`** — no reference contexts on that run either.
   Same fix.
3. **Verifier latency is real (~45s mean)** — that's the price of the hard gate on
   free CLI. Acceptable per the tradeoff; if an interviewer asks, point at
   `eval/results/verifier_tradeoff.png`.
4. **Role model is a demo** — `external_contractor` is restricted from the
   *financial sector* on publicly-available filings to illustrate the mechanism;
   in a real deployment the restriction would come from an actual contract/acl, not
   a sector heuristic.

---

## Verification checklist for Phase 5 sign-off

- [ ] `git push` includes `f7225db` → workflow file visible in GitHub UI
- [ ] `.github/workflows/eval-gate.yml` appears in Actions / runs on a test PR
- [ ] Live demo URL resolves and answers one in-scope + one out-of-scope question
      (role selector + faithfulness badge visible)
- [ ] (if 5.3 done) real Haiku-API $ logged, total < $5, case-study cost updated
- [ ] README has the live link (or explicitly says "local demo + screenshots")

## Suggested first prompt for the next session

> "Finish the Phase 5 publishing tail (auth refresh + push the workflow), then
> deploy the live demo following NEXT_PHASE_CHECKLIST.md and update the README
> with the URL. Log the real Haiku-API spend if quick. Update PHASE_5_HANDOVER.md
> with what you did."