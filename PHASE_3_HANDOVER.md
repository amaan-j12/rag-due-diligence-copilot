# Phase 3 Handover — Session 6

**Status:** Phase 1 (retrieval + generation + eval) complete. Phase 2 (faithfulness verifier) complete and tested — see `PHASE_2_HANDOVER.md` and `eval/verification_report.md`. This document scopes Phase 3.

---

## A note on phase numbering (read this first)

There are two different "Phase 3" definitions floating around this project's docs, written at different times:

1. **`BUILD_SPEC.md` Part 6/7** (the original architecture spec) — RBAC-aware retrieval and CI eval regression gating. These were always meant to come after the core retrieval+generation+verifier system existed.
2. **`NEXT_PHASE_CHECKLIST.md`** ("Phase 3 Deployment Checklist") — written *before* the faithfulness verifier existed, scopes cloud deployment (Docker, GCP/AWS, monitoring).

Both are legitimate next work — they're not in conflict, just written at different points with different framing. **This document's recommendation: do RBAC + CI gating first** (Part 6/7 below), because they're cheap, they finish the interview narrative `BUILD_SPEC.md` was built around, and the mechanism for RBAC already half-exists in the codebase (see below) — deployment infrastructure is comparatively generic DevOps work that doesn't differentiate this project the way RBAC-aware retrieval and a faithfulness-gated CI pipeline do. If you want deployment first instead, `NEXT_PHASE_CHECKLIST.md` is ready to execute as-is and doesn't depend on anything in this document.

---

## What's already done (as of Session 6)

| Component | Status | Where |
|---|---|---|
| Corpus (90 filings, 10K+ chunks) | ✅ | `data/chunks.jsonl` |
| Hybrid retrieval (dense + BM25 + RRF + rerank) | ✅ | `src/retrieval/retriever.py` |
| Generation (CLI-first, API-fallback) | ✅ | `src/generation/generate.py` |
| RAGAS eval (naive baseline scored) | ✅ Partial | `eval/run_ragas.py`, `eval/results/` |
| Faithfulness verifier | ✅ | `src/verification/faithfulness_verifier.py` |
| Verifier test suite (12 fixtures + 30-question audit) | ✅ | `eval/test_verification.py`, `eval/run_verification_audit.py` |
| Streamlit app with verification badge | ✅ | `app.py` |
| RBAC filter mechanism (not policy) | ⚠️ Half-done | `src/retrieval/retriever.py` — see below |
| CI eval regression gate | ❌ Not started | — |

---

## Part A — Finish RBAC-aware retrieval (`BUILD_SPEC.md` Part 6)

### What already exists

The *mechanism* is already built, from Phase 1:

```python
# src/retrieval/retriever.py
def dense_search(self, query, top_k=None, role=None):
    ...
    if role:
        query_filter = Filter(must=[FieldCondition(key="allowed_roles", match=MatchAny(any=[role]))])
    results = self.qdrant.query_points(..., query_filter=query_filter)
```

Every chunk already carries an `allowed_roles: ["analyst", "compliance"]` field (added at ingestion, Phase 1). The filter runs **inside the Qdrant query itself**, not as a post-processing step — meaning a document a role isn't allowed to see is never even retrieved, not retrieved-then-discarded. This is the correct enforcement point (documented in `PHASE_1_DETAILED_WALKTHROUGH.md`, Step 7) and it's already correct. What's missing is the *policy*: right now every chunk allows both roles, so the filter has nothing to actually restrict.

### What's missing

1. **A real policy that differentiates roles.** `BUILD_SPEC.md` Part 6 suggests: `analyst` (sees all public filings), `external_contractor` (sees a restricted subset — tag some filings "internal-only" even though EDGAR data is public, purely to demonstrate the mechanism), `compliance` (sees everything + audit logs).
2. **Applying `allowed_roles` at chunking/indexing time based on the policy** — likely by ticker or sector, since that's the natural boundary a due-diligence tool would restrict on (e.g. a contractor engaged for a specific deal shouldn't see unrelated companies' filings).
3. **Wiring the BM25 side too** — the Qdrant filter is done, but `bm25_search()` doesn't currently filter by role at all. A role-restricted query could still surface a restricted chunk via the keyword path. Check `src/retrieval/retriever.py`'s BM25 method and add the equivalent pre-filter (filter the candidate chunk-ID list by `allowed_roles` before scoring, not after).
4. **Audit logging.** `BUILD_SPEC.md`: "Log every query with the requesting role and which documents were eligible vs. actually retrieved." Currently nothing logs this. A simple JSONL append (matching the existing `logs/api_usage.jsonl` pattern) would suffice — role, query, timestamp, chunk_ids retrieved.
5. **The demo screenshot `BUILD_SPEC.md` explicitly asks for:** "a side-by-side example — same question asked as `analyst` vs `external_contractor`, showing different retrieved chunks and different (correctly scoped) answers." This is called out as doing "more work than paragraphs of explanation" for the portfolio narrative — don't skip it.

### Estimated effort: 3-5 hours
- Define policy + tag chunks: 1-1.5h (likely a small script re-processing `data/chunks.jsonl`'s `allowed_roles` field — no re-embedding needed, since it's metadata, not vector content)
- BM25 pre-filter: 30-45 min
- Audit logging: 30-45 min
- Demo screenshot + narrative writeup: 30 min

---

## Part B — CI eval regression gate (`BUILD_SPEC.md` Part 7)

### What this is

A GitHub Actions workflow that runs the RAGAS/verifier eval suite automatically whenever a PR touches the prompt template (`config/prompt_templates.yaml`), chunking config, or retrieval parameters, and **fails the PR** if any metric drops below a threshold (e.g. faithfulness < 0.85, verifier catch rate < 80%). This is the concrete "LLMOps" artifact `BUILD_SPEC.md` calls out — not a demo, an actual gate that would have caught a real regression if you introduced one.

### What's needed

1. **This repo needs to actually be a git repository first** — it currently isn't (checked at session start: "Is a git repository: false"). GitHub Actions requires a GitHub repo. This is a prerequisite, not optional.
2. **A cheap eval subset for CI** — the full 30-question RAGAS + verifier suite costs real time and (if judged via API) money; running it on every PR needs a fast, cheap subset. Recommend: 5-8 questions covering all 3 categories (single-hop, multi-hop, out-of-scope), scored via the free claude-CLI path already built.
3. **A threshold config** — where do the pass/fail numbers live? Suggest a small YAML (`eval/ci_thresholds.yaml`) so thresholds are versioned and reviewable in a diff, same reasoning as why `prompt_templates.yaml` is a config file and not a hardcoded string (Phase 1, Step 8).
4. **The actual workflow file** (`.github/workflows/eval-gate.yml`) — checkout, install deps, run the eval subset, compare against thresholds, fail the job if any metric is below bar.
5. **A real regression to screenshot.** `BUILD_SPEC.md`: "screenshot a red CI run that caught a real regression during your own development, if one happens (it likely will)." Don't manufacture one artificially before you have real changes to make — but when you do touch the prompt template or retrieval params next, let the gate actually catch something if it does, and keep that screenshot.

### Estimated effort: 3-4 hours
- Git init + GitHub repo setup: 15-30 min (needs your explicit go-ahead — this is a "make repo state visible to others" action, confirm before doing it)
- CI eval subset + threshold config: 1-1.5h
- GitHub Actions workflow: 1-1.5h
- Test it actually fails on a deliberately broken change, then fix and confirm it passes: 30-45 min

---

## Part C — Verifier follow-ups (from `eval/verification_report.md`, Session 6)

Two known limitations were documented, not fixed, in Phase 2 — listed there as Phase 3 items:

1. **Mixed real+refusal answers get entirely skipped.** 3 multi-hop comparison questions (`q13`, `q22`, `q24`) mix a real, checkable answer for one company with a legitimate refusal for another (missing filings) in the same response. The current `is_refusal()` check is binary — it skips the whole answer rather than just the refused portion. Fix: segment the answer by company/section before running refusal detection, verify each segment independently.
2. **Retrieval-composition meta-claims read as unsupported.** 2 questions (`q07`, `q21`) score low not from hallucination but because the answer includes true statements about *what the retrieval returned* (e.g. "the retrieved context contains no quantitative interest-rate data for Morgan Stanley") — these aren't document claims, so entailment checking against document text can't confirm them. Fix: classify claims as "about document content" vs. "about the retrieval/pipeline itself" before scoring, and only verify the former.

### Estimated effort: 2-3 hours combined
Both are refinements to `src/verification/faithfulness_verifier.py`'s claim-extraction step — likely a single combined fix (extend the extraction prompt to tag each claim's type, then route document-claims to entailment checking and skip/report pipeline-claims separately) rather than two separate changes.

---

## Part D — Latency (optional, cost tradeoff — needs your call, not mine)

The verifier currently runs at median 58.8s per verification (claude CLI subprocess). This is disclosed and explained in `eval/verification_report.md`, not hidden — but it's real, and if the live app needs to feel responsive, it's worth knowing the actual options rather than assuming CLI is the only path:

| Option | Latency | Cost | Note |
|---|---|---|---|
| Current (claude CLI) | ~58s median | $0 | What's live now |
| Anthropic API (Haiku) | Likely 2-5s | ~$0.01-0.04/verification (per Phase 1's naive-pass judge cost data) | No subprocess spawn, no CLI startup overhead |
| Async/parallel verification | Same per-call latency, but doesn't block the UI | $0 | Run verification in the background after showing the (unverified) answer, update the badge when done — a UX fix, not a latency fix |

This wasn't decided in Session 6 because it's explicitly a cost-vs-speed tradeoff the user needs to make, not something to default into. If Phase 3 prioritizes a snappier live demo, this is the first thing to revisit.

---

## What's NOT recommended for Phase 3 (yet)

- **Full deployment (Docker/cloud)** — `NEXT_PHASE_CHECKLIST.md` has this fully scoped already if you want it, but it's generic infrastructure work, not something that adds to the interview narrative the way RBAC/CI-gating do. Do it whenever you actually need a public-facing demo link, not before.
- **Fine-tuning the reranker / hand-labeled eval set / corpus expansion** — these are real Phase 4 items (per `PHASE_2_HANDOVER.md`'s own recommendations), meaningful but not close to done-able cheaply; don't pull them forward.
- **Hybrid/rerank RAGAS re-scoring** — Phase 1 has partial NaN scores for hybrid/rerank passes due to earlier concurrent-judge timeouts. Optional cleanup, not blocking, and not narratively important now that the verifier (a stronger, more specific faithfulness check) exists.

---

## Recommended order

1. **Part A (RBAC)** — cheapest, most narratively valuable, mechanism already exists.
2. **Part C (verifier follow-ups)** — cheap, closes out Phase 2's own documented gaps before starting something new.
3. **Part B (CI gate)** — needs the git-repo prerequisite; do this once you're actually making further prompt/retrieval changes worth gating.
4. **Part D (latency)** — only if the live demo experience actually needs to be fast; ask before spending API budget on it.

**Total estimated effort for Parts A + B + C: ~8-12 hours**, comparable in scope to Phase 2.

---

## Success criteria for Phase 3

✅ Phase 3 is done when:
1. RBAC policy is real (not a placeholder) — at least 2 differentiated roles, enforced at both dense and BM25 retrieval, with an audit log.
2. A side-by-side same-question/different-role screenshot exists for the case study.
3. CI eval gate exists, runs on relevant PRs, and has caught (or would catch) a real regression.
4. The two documented verifier limitations from Phase 2 are either fixed or explicitly re-scoped to Phase 4 with reasoning (not silently dropped).
5. Interview narrative updated: RBAC's "prove it was right — audit trail, named owner, source sentence → answer" claim from `BUILD_SPEC.md` is now backed by a real screenshot and real logs, not just a mechanism.

---

*Handover prepared: Session 6, immediately following Phase 2 completion.*
*See `PHASE_2_DETAILED_WALKTHROUGH.md` for exactly how the verifier was built, bugs and all.*
