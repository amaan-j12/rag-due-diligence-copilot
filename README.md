# RAG Due Diligence Copilot

M&A / equity-research due-diligence assistant over 90 SEC EDGAR filings (30 companies,
tech / pharma / financial services). Built as a portfolio artifact demonstrating
production-grade RAG engineering: section-aware chunking, hybrid retrieval (BM25 +
dense + RRF), cross-encoder reranking, versioned prompts, and RAGAS-based evaluation.

See `BUILD_SPEC.md` for the full engineering spec and `PHASE_2_HANDOVER.md` for
current build status, measured eval numbers, and the Phase 2 (faithfulness verifier)
task list.

## Quickstart

```bash
source venv/bin/activate
streamlit run app.py
```

## Repo layout

- `src/ingestion/` -- HTML cleaning of raw EDGAR filings
- `src/chunking/` -- section-aware chunker + chunks.jsonl builder
- `src/indexing/` -- Qdrant (local embedded) + BM25 index builders
- `src/retrieval/` -- dense/BM25 search, RRF fusion, cross-encoder reranking
- `src/generation/` -- prompt assembly + Claude CLI/API generation
- `config/` -- versioned prompt templates + retrieval/embedding config
- `eval/` -- 30 eval questions, RAGAS runner, before/after chart
- `app.py` -- local Streamlit demo
