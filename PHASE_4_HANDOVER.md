# Phase 4 Handover — Session 7

**Status:** Phases 1–3 complete and committed to git (`main`, initial commit `6c98606`).
This document scopes Phase 4: final deployment / cost verification, the case study,
and publishing. Run the items in order — each is small and independently verifiable.

---

## Project in one line

A RAG due-diligence copilot over 90 SEC EDGAR 10-K/10-Q filings (30 companies,
3 sectors), with role-aware retrieval (RBAC), a faithfulness verifier hard gate,
RAGAS eval with a CI regression gate, and per-query trace observability — all
running on free backends ($0 runtime cost).

---

## What's done (verified, committed)

| Component | Status | Where |
|---|---|---|
| Corpus: 90 filings, 10,027 chunks, 39M fallback index | ✅ committed | `data/chunks.jsonl`, `data/manifest.json` |
| Hybrid retrieval: dense / BM25 + RRF / + cross-encoder rerank | ✅ | `src/retrieval/retriever.py`, `config/retrieval_config.yaml` |
| Generation: opencode CLI big-pickle → claude CLI → (blocked) API fallback | ✅ | `src/generation/generate.py`, `config/prompt_templates.yaml` |
| RBAC: per-chunk `allowed_roles`, pre-retrieval filter (Qdrant + BM25), audit log | ✅ | `src/rbac/`, `config/rbac_policy.yaml`, `logs/retrieval_audit.jsonl` |
| Faithfulness verifier: claim-type tagging, mixed-answer scoring, refusal fast-path, opencode backend | ✅ | `src/verification/faithfulness_verifier.py` |
| Verifier tests: 12 fixtures 100% catch; 30-question audit 30/30 pass, 0.995 avg | ✅ | `eval/test_verification.py`, `eval/results/verification_batch_tests_v3.json`, `records_hybrid_v3.json` |
| CI eval gate: 8-question subset + thresholds + GitHub Actions workflow | ✅ | `eval/ci_eval_gate.py`, `eval/ci_subset.json`, `eval/results/ci_snapshot.json`, `config/ci_thresholds.yaml`, `.github/workflows/eval-gate.yml` |
| Observability: per-query trace JSONL (stage timings/tokens/cost), verifier ON–OFF tradeoff chart | ✅ | `src/observability/trace.py`, `eval/plot_tradeoff.py`, `eval/results/verifier_tradeoff.png` |
| RBAC side-by-side demo (analyst vs external_contractor, same question) | ✅ | `scripts/rbac_demo.py`, `eval/results/rbac_demo.json` |
| Streamlit demo app | ✅ | `app.py` |

**Key measured numbers** (source: `eval/verification_report.md`, `eval/results/*`):
- Verifier injected-error catch rate: **12/12 = 100%** (12 fixture cases).
- Full 30-question audit: **30/30 pass** (9 out-of-scope skipped correctly),
  avg faithfulness **0.995**, avg latency **45.4s/query** (opencode CLI).
- Generation latency (from `logs/api_usage.jsonl` timestamps): **median 14s, p75 30s**.
- CI gate subset: 8 questions, **1.000 pass rate** on scored, **1.000 out-of-scope
  dismissal**, **0 call failures**, ~36s avg latency per verified answer.
- Tradeoff (Haiku-equiv API pricing, actual = $0 on free CLI): verifier OFF
  $0.0077/query & 0% catch; verifier ON $0.0141/query & 100% catch. Latency
  OFF ~14s vs ON ~59s. Chart: `eval/results/verifier_tradeoff.png`.

---

## Phase 4 scope (BUILD_SPEC.md Part 9, Phase 4)

1. **Final deployment + $ cost verification.** The locked decision is "Haiku-API
   version, stay under $5". So far the build runs on free backends and has logged
   $ spend: `logs/api_usage.jsonl` (api_usage shows only API-fallback rows; the
   CLI rows are logged at $0.00). Verify true $ spent, decide whether to (a) keep
   the free-CLI deployment and report $0 with the API-equivalent estimate from the
   tradeoff chart, or (b) run one real Haiku-API pass and log its cost. See
   "Cost verification" below.
2. **Case study** (`docs/CASE_STUDY.md`): problem (with the 73% / 89% citation
   stats), architecture diagram, what broke and what was fixed (Apple ambiguity,
   QA-in-DEBUG upstream, deepseek retirement, entity-hint mislead), before/after
   RAGAS chart, RBAC demo screenshot/narrative, verifier tradeoff chart, CI gate
   as the LLMOps artifact. All charts already exist.
3. **Publish**: push to GitHub (`gh repo create`, `git push -u origin main`),
   README polish, live demo link (Streamlit Community Cloud or local screenshot;
   GCP/Docker optional per `NEXT_PHASE_CHECKLIST.md`).

---

## Step 1 — Final deployment & cost verification

- Check `logs/api_usage.jsonl`: `source == "anthropic_api"` rows carry real
  `cost_usd`. Sum them, plus the CLI rows (logged at $0). If the total is > $0,
  decide whether that's acceptable ($5 budget) or re-point everything at the free
  CLI (already the default once `_try_opencode_cli` succeeds — the API fallback
  only fires if both CLIs fail, so it should be near-zero anyway).
- Record the result in `docs/CASE_STUDY.md` under "Cost".
- Optional but portfolio-strong: run the 30-question RAGAS suite (or the CI
  subset) once against the Haiku **API** directly and log the real $ — this makes
  the "under $5" claim concrete. Budget the run first (30 questions × ~$0.01–0.02 ≈
  < $1 at Haiku pricing per the tradeoff estimate).

## Step 2 — Case study

Template to fill (all data is in `eval/verification_report.md` and `eval/results/`):

1. **Problem** — LLM citations hallucinate; RAG systems surface documents across
   permission boundaries. Cite NIST 73% multi-hop failure and 89%-of-Latin-claims-
   wrong source stat if in your notes; else use your repo's verifier catch-rate
   evidence.
2. **Architecture** — 2–3 paragraphs + ASCII diagram: ingestion → chunking →
   embedding/index → RBAC pre-filter → hybrid retrieval → rerank → generation →
   faithfulness gate → answer. Mention free-CLI backend decision and why.
3. **What broke & what I fixed** — the real development failures (this is the
   strongest part of a portfolio):
   - Apple-filename ambiguity (get `Item 1` not `Item 1A`) — Phase 1.
   - QA-in-DEBUG upstream bug surfacing in eval — Phase 1.
   - Claude Code session limit → switched verifier/backend to opencode CLI.
     `opencode/deepseek-v4-flash-free` retired → moved to `opencode/big-pickle`.
   - Mixed real+refusal answers skipped wholesale → claim-type tagging
     (document/pipeline/refusal).
   - Retrieval meta-claims ("context contains no X") dragged score down →
     `pipeline` type, reported not scored.
   - Single-company entity hint misleading the judge on multi-company
     comparisons → entity only attached when all chunks share one ticker.
4. **Before/after numbers** — RAGAS chart `eval/results/before_after_ragas.png`,
   verifier catch rate, audit numbers.
5. **RBAC demo** — `eval/results/rbac_demo.json`: same question, analyst gets
   financial-sector JPM chunks + full answer; external_contractor gets only
   tech/pharma chunks and refuses correctly. Quote both answers.
6. **Cost/latency tradeoff** — `eval/results/verifier_tradeoff.png` + the
   citation-halo argument: hard gate costs ~45s & ~$0.006–0.014/query for 100%
   catch.
7. **CI gate as LLMOps artifact** — describe the workflow, thresholds, snapshot
   refresh flow. Screenshot the failed-PR case if possible (you already verified
   the gate exits non-zero on a regression snapshot).

## Step 3 — Publish

- `gh repo create rag-due-diligence-copilot --public --source=. --push`
  (or via GitHub web UI, then `git remote add origin ... && git push -u origin main`).
- Polish `README.md`: what it is, quick start, screenshots (app, tradeoff chart,
  RBAC demo), links to case study + eval report. Note the free backend.
- Deploy demo: Streamlit Community Cloud is the cheapest (free for public repos) —
  but note it can't hit a local Qdrant/SentenceTransformer at full size. Options:
  (a) copy `data/` + models into a Docker image and use a $0-tier cloud VM,
  (b) keep it "local demo" and publish notebook/screenshots instead. `NEXT_PHASE_CHECKLIST.md`
  has the Docker/GCP path if you want it. **Recommendation: don't block publishing
  on deployment — screenshots + a `make run` in the README is sufficient for the
  portfolio; add the hosted demo only if it's < 1h of work.**

---

## Notes & gotchas for the next session

- **CI snapshot refresh flow**: any change to prompt template / chunking /
  retrieval / verifier config must be followed by `python eval/ci_eval_gate.py --refresh`
  (real opencode-CLI verification, ~5 min for 8 questions) and the refreshed
  snapshot committed. CI compares the committed snapshot to thresholds with no
  LLM (GitHub Actions has no authenticated CLI).
- **Eval runner resumes, doesn't redo**: to re-verify the full 30-question set,
  use a fresh `--out` path (e.g. `records_hybrid_v4.json`), not the old one —
  it skips ids already present.
- **Entity-hint rule** (verifier): `entity` only when all retrieved chunks share
  one ticker. Never the first chunk's ticker for a multi-company answer.
- **Backend precedence**: opencode (free, primary) → claude CLI → Anthropic API
  (logs real cost). Keep ANTHROPIC_API_KEY unset to force the free path.
- **RBAC policy**: financial sector is restricted to `[analyst, compliance]`;
  tech/pharma also allow `external_contractor`. Enforcement is pre-retrieval.
- **Git identity** is set to `mohammadamaan@users.noreply.github.com` locally —
  change before pushing if you want a different author email on GitHub.
- `data/raw/`, `data/qdrant_db/`, `bm25_index.pkl`, `venv/`, logs, and eval
  scratch are git-ignored; `data/chunks.jsonl` (39M) IS committed.
- Repo is a local git repo only — no remote yet.

## Suggested first prompts for the next session

- "Run Step 1 (cost verification) and log the result; then write the case study
  to `docs/CASE_STUDY.md` following the Phase 4 handover template."
- "Push this repo to GitHub as a public repo with the polished README, and give
  me the URL."
- "Run the RAGAS suite once against the real Haiku API on the CI subset and log
  actual $; update the case study cost section with real numbers."