# RAG Due Diligence Copilot

A role-aware, citation-verified RAG assistant over 90 SEC EDGAR 10-K/10-Q filings
(30 companies across tech / pharma / financial services) for M&A and equity-research
due diligence.

Built as a portfolio artifact demonstrating production-grade RAG engineering —
including the layers most demos skip: **RBAC enforced at retrieval time**, a
**faithfulness verifier hard gate** before answers ship, an **LLMOps CI regression
gate**, and per-query **cost/latency observability**.

## Headline results

- Faithfulness verifier catches **100% (12/12) of injected hallucinated-citation answers** (first build: 92%).
- 30-question audit: **30/30 pass**, avg faithfulness **0.995**, 0/42 call failures.
- **RBAC at the index, not the answer**: an `external_contractor` querying JPMorgan's litigation exposure retrieves zero financial-sector chunks and correctly refuses.
- **$0 runtime cost** on free opencode CLI models (Phase-1 API spend: $1.24 total, under the $5 budget).
- CI gate fails PRs when faithfulness falls below `config/ci_thresholds.yaml`.

See **[docs/CASE_STUDY.md](docs/CASE_STUDY.md)** for the full writeup: problem
(citation-halo + IAM-at-ingestion failure modes), architecture, 7 real failures
found and fixed during development, before/after numbers, and the tradeoff chart.

## Quickstart

```bash
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Build indexes (downloads models; Qdrant + BM25 built locally, ~2GB)
python -m src.ingestion.clean
python -m src.chunking.build_chunks
python -m src.indexing.build_qdrant_index
python -m src.indexing.build_bm25_index

# Interactive demo (role selector, verifier badge)
streamlit run app.py

# RBAC side-by-side: same question as analyst vs external_contractor
python scripts/rbac_demo.py
```

Requires the `opencode` CLI authenticated with a free model (`opencode/big-pickle`)
for generation and verification. Full reproduction steps in the case study (§9).

## Pipeline

```
filings -> clean -> section-aware chunk (metadata: ticker/section/period/url)
  -> bge-base-en-v1.5 -> Qdrant (dense)  +  BM25 (sparse)
  -> RBAC pre-filter (Qdrant payload + BM25, NOT post-generation)
  -> RRF fusion -> cross-encoder rerank (top-5)
  -> versioned prompt -> generation (free CLI)
  -> faithfulness verifier (hard gate: document/pipeline/refusal claim types)
  -> answer + citations + pass/fail
```

## Repo layout

- `src/ingestion|chunking|indexing/` — EDGAR cleaning, section-aware chunker, Qdrant + BM25 builders
- `src/retrieval/` — dense/BM25 search, RRF fusion, cross-encoder reranking, retrieval audit log
- `src/rbac/` — role policy + chunk retagging, `config/rbac_policy.yaml`
- `src/generation/` — versioned prompt assembly + free-CLI generation (opencode → claude → API fallback)
- `src/verification/` — faithfulness verifier (claim extraction + entailment, one call)
- `src/observability/` — per-query trace JSONL (stage timings / tokens / est. cost)
- `config/` — versioned prompt templates, retrieval/embedding config, RBAC policy, CI thresholds
- `eval/` — 30-question set, 12 injected-error fixtures, RAGAS runner, verification audit, CI gate
- `.github/workflows/eval-gate.yml` — LLMOps CI gate
- `docs/CASE_STUDY.md` — the portfolio writeup

## Eval / CI

```bash
python eval/ci_eval_gate.py --refresh    # re-verify 8-Q subset, rewrite snapshot (needs CLI)
python eval/ci_eval_gate.py              # threshold check (what CI runs)
python eval/run_verification_audit.py --source test_cases --start 0 --end 12 --out eval/results/verification_batch_tests_v3.json
python eval/plot_tradeoff.py             # verifier ON/OFF accuracy-cost chart
```

Key docs: `BUILD_SPEC.md` (engineering spec), `eval/verification_report.md`
(verifier before/after), `PHASE_4_HANDOVER.md` (status + remaining work).