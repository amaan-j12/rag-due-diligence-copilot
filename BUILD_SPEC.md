# M&A Due Diligence Copilot — Full Build Spec

**What this is:** A production-grade RAG system over SEC EDGAR filings, built to demonstrate the exact engineering maturity enterprises say they can't find in junior candidates. Framed as a portfolio artifact — persona is an M&A/equity-research analyst who needs to cross-reference risk factors, litigation, and financials across dozens of filings without getting a confidently-wrong answer.

**Why this spec exists:** Built from deep research into (1) 12 synthesized YouTube transcripts on 2026 AI-engineer hiring, (2) live 2026 data on RAG production failure rates pulled from Reddit/HN/industry postmortems, (3) real pricing/free-tier data for every tool in the stack. See `AI_ENGINEER_PORTFOLIO_PLAN.md` in this folder for the upstream research this spec implements.

**Locked decisions (from scoping conversation, 2026-08-19):**
- Corpus: SEC EDGAR filings (10-K/10-Q)
- Generation: Claude CLI / opencode CLI during dev; Claude API (Haiku-class) at deploy time only if projected cost stays under $5 total
- Scope: full system, phased — MVP first, enterprise layer second
- Framing: pure portfolio artifact, no real client dependency

---

## Part 0 — The problem this actually solves (memorize this, it's your interview answer)

Research finding, not opinion: **70-80% of enterprise RAG deployments fail before reaching production**, and a July 2026 NIST analysis found **73% fail informal accuracy audits on multi-hop queries**. The most damning finding: **73% of RAG answers with citations contained at least one factual error, but 89% of human reviewers rated those same answers "likely accurate"** — purely because a citation was present. Citations create a false-confidence halo; they don't guarantee correctness.

Three structural gaps this system is built to close, each backed by a named finding:

1. **The citation halo effect** — citing a source doesn't mean the source supports the claim. Most RAG systems stop at "did we cite something," not "does the citation entail the claim." → Fixed by the **faithfulness verifier** (Part 5).
2. **No access control on retrieval** — most vector DBs are flat, permission-free indexes; IAM gets stripped at ingestion. In regulated industries, the requirement isn't "the model is usually right," it's "prove it was right — audit trail, named owner, source sentence → answer." → Fixed by **RBAC-aware retrieval** (Part 6).
3. **Domain-specific chunking failures** — e.g., a financial-doc RAG system returning Apple's consumer-product info when asked about Apple's capital structure, because "Apple" appears in fragments without disambiguating context. This is *why* SEC filings are the right corpus: the failure mode will actually show up in your data, and you document how you caught and fixed it.

Your interview narrative in one sentence: *"73% of production RAG answers with citations are still wrong, and reviewers trust them anyway because of the citation itself — I built a verification layer that catches that specific failure, with a dashboard showing the accuracy/cost tradeoff of running it."*

---

## Part 1 — System architecture (full picture)

```
                                   ┌─────────────────────┐
                                   │   SEC EDGAR filings  │
                                   │  (10-K/10-Q, 20-30    │
                                   │   companies, XBRL+HTML)│
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │   Ingestion pipeline   │
                                   │  fetch → clean → parse │
                                   │  content-hash diffing  │  ← incremental (v2)
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │   Chunker              │
                                   │  500-800 tok, 100 ovlp │
                                   │  section-aware splits  │
                                   └──────────┬───────────┘
                          ┌───────────────────┼───────────────────┐
                          │                                       │
               ┌──────────▼──────────┐               ┌────────────▼───────────┐
               │  Embedding model     │               │   BM25 index            │
               │  → Qdrant (dense)    │               │   (rank_bm25 / ES OSS)  │
               │  + RBAC metadata     │               │   + RBAC metadata       │
               └──────────┬──────────┘               └────────────┬───────────┘
                          │                                       │
                          └───────────────┬───────────────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  RBAC filter (pre-query) │  ← enforced BEFORE retrieval,
                              │  role → allowed doc IDs  │     not after generation
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  Hybrid fusion (RRF)     │
                              │  dense + BM25 → top-20   │
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  Cross-encoder reranker  │
                              │  top-20 → top-5          │
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  Query router (v2)       │
                              │  single-hop / multi-hop / │
                              │  out-of-scope             │
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  Prompt template          │
                              │  (versioned config file)  │
                              │  mandatory citation format│
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  LLM generation           │
                              │  Claude CLI/opencode (dev) │
                              │  Claude Haiku API (deploy) │
                              └───────────┬────────────┘
                                          │
                              ┌───────────▼────────────┐
                              │  Faithfulness verifier    │  ← THE differentiator
                              │  entailment check per      │
                              │  citation → pass/flag/reject│
                              └───────────┬────────────┘
                                          │
                       ┌──────────────────┼──────────────────┐
                       │                                     │
            ┌──────────▼──────────┐              ┌───────────▼───────────┐
            │  Answer + citations  │              │  Langfuse trace         │
            │  + verifier verdict   │              │  every stage logged,    │
            │  → Streamlit/Gradio   │              │  cost + latency per hop │
            └───────────────────────┘              └─────────────────────────┘

   Parallel, always-on:
   ┌────────────────────────────────────────────────────────────────┐
   │  RAGAS eval harness (30-50 Qs incl. multi-hop) → CI gate on every │
   │  prompt/chunking/retrieval config change (GitHub Actions)         │
   └────────────────────────────────────────────────────────────────┘
```

---

## Part 2 — Full tech stack, with real costs

| Layer | Tool | Cost | Notes |
|---|---|---|---|
| Orchestration | LlamaIndex | Free, OSS | Chunking, index management, query engine glue |
| Vector store | Qdrant Cloud | **Free** — permanent free tier, no card, 1GB RAM/4GB disk, ~1M vectors @768-dim | More than enough for 20-30 companies of filings |
| Keyword index | `rank_bm25` (Python) or Elasticsearch OSS | Free, OSS | Simpler to self-host: `rank_bm25` in-process, no server |
| Embeddings | `sentence-transformers` (e.g. `bge-base-en-v1.5`) or Voyage/OpenAI embeddings | Free if local (sentence-transformers); ~$0.02/1M tokens if hosted | Recommend local — zero cost, good enough quality, avoids a second paid API |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (sentence-transformers) | Free, runs locally | Top-20 → top-5 |
| Generation (dev) | Claude CLI / opencode CLI | Already have | Use during build/iteration |
| Generation (deploy) | Claude Haiku API | ~$0.25/$1.25 per M tokens in/out — a few hundred eval calls will land well under $5 | Track actual spend in Langfuse; stop before $5 |
| Eval | RAGAS | Free, OSS | Faithfulness, answer relevancy, context precision/recall |
| Faithfulness verifier | RAGAS faithfulness metric (reused) or a small NLI model (`cross-encoder/nli-deberta-v3-base`) | Free, OSS | Runs post-generation, per-citation |
| Observability | Langfuse Cloud (Hobby tier) | **Free** — 50k events/mo, 2 users | Don't self-host — infra overhead isn't worth it for a portfolio project |
| API layer | FastAPI | Free, OSS | Wraps the pipeline for the CI harness + optional API mode |
| Frontend/demo | Streamlit or Gradio | Free, OSS | Deploy target |
| CI | GitHub Actions | Free for public repos | Runs RAGAS on every PR touching prompt/chunk/retrieval config |
| Containerization | Docker | Free | Reproducibility, deploy portability |
| Deployment host | Hugging Face Spaces or Render free tier | Free | HF Spaces is the easier free path for a Streamlit/Gradio app |
| Storage (raw filings, logs) | Local disk during dev, Postgres if you add ingestion state tracking | Free | SQLite is fine for v1; Postgres only needed for v2 incremental ingestion state |

**Total unavoidable cost: under $5**, and only at deploy time, only for the Haiku API calls — everything else is genuinely free.

---

## Part 3 — Corpus and data details

- **Source:** [SEC EDGAR full-text search](https://www.sec.gov/edgar/search/) and the EDGAR REST API (`data.sec.gov`) — free, no API key required, rate-limited to 10 req/sec with a declared User-Agent header.
- **Selection:** 20-30 companies across 2-3 sectors (recommend: tech, pharma, financial services — sectors where risk-factor language and litigation disclosures are dense and comparably structured). Pull the most recent 10-K plus 1-2 recent 10-Qs per company.
- **Why not just one company:** the multi-hop, cross-document comparison questions ("compare litigation risk across Company A and Company B") are what expose the failure modes the research documents. A single-document corpus can't test this.
- **Format handling:** EDGAR filings are HTML with heavy boilerplate (navigation, XBRL tags, legal headers). Budget real time for a cleaning step — strip HTML, deduplicate repeated legal disclaimers, and preserve section headers (Item 1A "Risk Factors", Item 3 "Legal Proceedings", Item 7 "MD&A") as chunk metadata. This section-tagging is what lets you filter/boost by section at query time and is a concrete, demonstrable design decision.
- **The "Apple problem" to watch for:** company names that are also common words or product lines will produce ambiguous chunks. Don't pre-solve this — let it happen, document it when it does, and show your fix (e.g., prepending company ticker + fiscal period to every chunk before embedding). This becomes a real "failure I found and fixed" story, not a hypothetical.

---

## Part 4 — Chunking, embedding, and hybrid retrieval details

- **Chunk size:** 500-800 tokens, ~100 token overlap. Use LlamaIndex's `SentenceSplitter`, but split on section boundaries first (don't let a chunk span from "Risk Factors" into "Legal Proceedings").
- **Chunk metadata (critical — this is what RBAC and citations depend on):** company ticker, filing type, fiscal period, SEC section name, source URL, page/paragraph anchor if extractable, and (for Part 6) an `allowed_roles` field.
- **Embedding model:** `BAAI/bge-base-en-v1.5` (open, strong on retrieval benchmarks, runs on CPU acceptably for this corpus size). Document the model name/version explicitly — this matters for Part 4's "embedding drift" risk below.
- **Dual index:** the same chunk set goes into both Qdrant (dense) and a BM25 index (sparse/keyword). Financial figures, tickers, and exact legal terms are exactly what BM25 catches and dense embeddings blur.
- **Fusion method:** use Reciprocal Rank Fusion (RRF) to merge the dense top-K and BM25 top-K into one ranked top-20 before reranking — simpler and more robust than manually weighting scores from two different distributions.
- **Reranking:** cross-encoder scores all 20 candidates against the actual query (not just embedding similarity) and cuts to top-5. This is the single most-skipped step in junior RAG projects — document your before/after eval numbers specifically for this stage, isolated from the hybrid-search improvement.
- **Known failure mode to design against — embedding drift:** if you ever change the embedding model, every existing vector becomes incomparable to new queries unless you fully re-embed and re-index. Pin the model version in a config file and note in your case study what your re-indexing procedure would be. You don't need to build this, just document the decision — it's a real ops-maturity signal.

---

## Part 5 — Faithfulness verifier (the differentiator — build this carefully)

This is the component that makes the project exceptional rather than "another RAG demo." It directly answers the 73%-citations-are-wrong / 89%-trusted-anyway finding.

**What it does:** after the LLM generates an answer with citations, run a separate verification pass that checks — for each individual claim + its cited chunk — whether the chunk actually *entails* the claim (not just topically relates to it).

**How to build it (two viable approaches, pick one for v1):**
1. **RAGAS faithfulness metric, repurposed at answer-time (not just eval-time).** RAGAS's faithfulness score already does claim decomposition + entailment checking against context — normally you run it offline on your eval set, but you can also call it inline, per-response, before showing the answer to the user.
2. **A dedicated NLI (natural language inference) model** (e.g. `cross-encoder/nli-deberta-v3-base`) — decompose the generated answer into individual claims (simple sentence-splitting is enough for v1), pair each claim with its cited chunk, and classify entailment/neutral/contradiction. Flag anything not classified as entailment.

**What happens on a fail:** don't silently hide the answer — show it with a visible flag ("this claim is not fully supported by the cited source") so the analyst persona knows to verify manually. This mirrors what a real regulated-industry deployment would need: never let the system claim more confidence than it has earned.

**What to measure and report:** verifier pass rate across your eval set, false-citation catch rate (deliberately inject a few wrong citations into test cases and confirm the verifier catches them), and the added latency/cost of running this extra pass — feed this into your cost dashboard (Part 8) so you can show the accuracy/cost tradeoff explicitly.

---

## Part 6 — RBAC-aware retrieval

**Why this exists:** most production RAG systems index all documents into one flat, permission-free vector space — the single most-cited security gap in the research. Any user query can surface any document regardless of who's asking.

**Design for v1 (simple, defensible):**
- Define 2-3 mock roles for the portfolio demo: e.g. `analyst` (sees all public filings), `external_contractor` (sees only a restricted subset — simulate this by tagging some filings "internal-only" even though EDGAR data is public, to demonstrate the mechanism), `compliance` (sees everything plus audit logs).
- Every chunk gets an `allowed_roles` metadata field at ingestion.
- **Enforcement point matters:** filter by role *before* retrieval (as a Qdrant metadata filter + a BM25 pre-filter), not as a post-hoc check on the LLM's output. Filtering after generation means the model already saw restricted content — that's the exact "IAM stripped at ingestion" failure the research names.
- Log every query with the requesting role and which documents were eligible vs. actually retrieved — this is your audit trail.

**What to show in the case study:** a side-by-side example — same question asked as `analyst` vs `external_contractor`, showing different retrieved chunks and different (correctly scoped) answers. This one screenshot does more work than paragraphs of explanation.

---

## Part 7 — Eval harness and CI regression gating

- **Test set:** 30-50 hand-written questions against your actual corpus, split into three categories — single-hop factual lookup, multi-hop cross-document comparison, and out-of-scope (should be refused). The multi-hop category is what the NIST 73%-failure finding specifically targets — don't skip it.
- **Metrics (via RAGAS):** faithfulness, answer relevancy, context precision, context recall. Run this suite three times across your build to get a real before/after story: (1) naive vector-only retrieval, (2) + hybrid search, (3) + reranking. Save all three score sets — this three-way comparison chart is your single best portfolio visual.
- **CI gate:** a GitHub Actions workflow that runs the RAGAS suite on every PR touching the prompt template, chunking config, or retrieval parameters, and fails the PR if any metric drops below a set threshold (e.g. faithfulness < 0.85). This is the concrete "LLMOps" artifact — screenshot a red CI run that caught a real regression during your own development, if one happens (it likely will).
- **Prompt versioning:** keep the prompt template in a config file (YAML/JSON), not hardcoded in Python — version it alongside code so the CI gate can diff prompt changes against eval score changes.

---

## Part 8 — Observability and cost/latency tracking

- **Langfuse trace per request:** log every stage — query → RBAC filter → hybrid retrieval (with scores) → rerank (with scores) → generation → faithfulness verification — as one trace tree. This is what lets you say "when something goes wrong, I can point at the exact stage" in an interview.
- **Cost tracking:** log token counts and $ cost per stage (embedding, generation, verification) per request. Report aggregate $/query and P50/P95 latency per stage in your case study.
- **The tradeoff chart to build:** accuracy (faithfulness pass rate) vs. cost, comparing "verifier on" vs. "verifier off" — this operationalizes the citation-halo finding into a real engineering decision a team would actually have to make.

---

## Part 9 — Phased build plan

### Phase 1 — MVP core (~1.5 weeks)
1. Pull and clean 20-30 companies' 10-K/10-Q filings from EDGAR.
2. Build section-aware chunker with full metadata (ticker, section, period, source URL).
3. Embed with `bge-base-en-v1.5`, index into Qdrant.
4. Build BM25 index over the same chunks.
5. Implement RRF fusion + cross-encoder reranker.
6. Prompt template (versioned config) with mandatory citation format.
7. Generation via Claude CLI/opencode during dev.
8. Write 30-50 eval questions (incl. multi-hop + out-of-scope).
9. Run RAGAS: naive-vector-only baseline → hybrid → hybrid+rerank. Produce the before/after chart.
10. Deploy a bare Streamlit demo.

### Phase 2 — The differentiator (~3-4 days)
11. Build the faithfulness verifier (Part 5). Wire it as a hard gate before answers ship.
12. Deliberately inject a few wrong-citation test cases; confirm the verifier catches them (this is your "I broke it on purpose" evidence, same pattern as project #2).
13. Add verifier pass-rate to your eval report.

### Phase 3 — Enterprise layer (~1 week)
14. Add RBAC metadata + pre-retrieval filtering (Part 6). Build the analyst-vs-contractor comparison demo.
15. Wire Langfuse tracing across every stage (Part 8).
16. Add cost/latency logging and the accuracy-vs-cost tradeoff chart.
17. Set up the GitHub Actions CI gate running RAGAS on config-changing PRs (Part 7).

### Phase 4 — Polish and case study (~2-3 days)
18. Deploy the final Haiku-API version (check actual $ spend stays under $5, log it).
19. Write the case study: problem (with the 73%/89% citation stat cited), architecture, what broke and what you fixed (Apple-ambiguity, any real CI catches), before/after numbers, RBAC demo screenshot, cost/latency dashboard screenshot.
20. Publish repo + live demo link + case study.

---

## Part 10 — Repo structure

```
rag-due-diligence-copilot/
├── README.md                    # short, links to full case study
├── CASE_STUDY.md                # the actual portfolio document
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .github/workflows/eval-gate.yml
├── config/
│   ├── prompt_templates.yaml    # versioned prompts
│   └── retrieval_config.yaml    # chunk size, top-k, model names — pinned versions
├── src/
│   ├── ingestion/                # EDGAR fetch + clean + section-tag
│   ├── chunking/
│   ├── indexing/                 # Qdrant + BM25 build scripts
│   ├── retrieval/                # RRF fusion, reranker
│   ├── rbac/                     # role filter logic
│   ├── generation/                # prompt assembly, Claude CLI/API call
│   ├── verification/              # faithfulness verifier
│   └── observability/             # Langfuse wiring, cost/latency logging
├── eval/
│   ├── questions.jsonl            # 30-50 test questions, tagged by category
│   └── run_ragas.py
├── app.py                         # Streamlit/Gradio demo
└── tests/
```

---

## Part 11 — What goes in the case study (portfolio presentation)

Recruiters spend 7-10 seconds on first pass — lead with a number, not prose.

1. **One-line problem statement** with the citable stat: "73% of production RAG answers with citations contain factual errors, and 89% of reviewers trust them anyway (2026 finding). This system catches that."
2. **Before/after eval chart** — naive vector-only → hybrid → hybrid+rerank, all four RAGAS metrics.
3. **Faithfulness verifier catch-rate** — how often it correctly flagged an injected bad citation.
4. **RBAC demo screenshot** — same question, two roles, two different scoped answers.
5. **Cost/latency dashboard screenshot** — $/query, P50/P95 per stage, verifier-on vs. verifier-off tradeoff.
6. **"What broke" section** — the Apple-ambiguity chunking issue (or whatever real failure you hit), what you saw, what you changed, the before/after eval delta from that specific fix.
7. **Architecture diagram** (adapt the one in Part 1).
8. **Live demo link** + **repo link**, both above the fold.

---

## Part 12 — Risks and open questions to resolve while building

- **EDGAR rate limits:** 10 req/sec with a declared User-Agent — build a small delay/retry wrapper into ingestion, don't hammer it.
- **Claude CLI/opencode cost during dev:** confirm your existing CLI usage doesn't count against a metered API budget before running the full 30-50-question eval suite repeatedly — if it does, run RAGAS scoring itself with a cheaper/local judge model and reserve Claude calls for final validation passes.
- **Multi-hop question difficulty:** writing genuinely hard multi-hop questions is harder than it sounds — budget real time for this, it's the category the 73% NIST failure stat is about, so a weak multi-hop test set undersells your best differentiator.
- **HF Spaces free tier resource limits:** local embedding + reranker models may be slow on free CPU tiers — test deploy early (Phase 1, not Phase 4) to catch this before it becomes a last-minute blocker.

---

## Sources this spec is built from

- [7 RAG Enterprise Failures Costing $4.7M in 2026](https://ragaboutit.com/7-rag-enterprise-failures-costing-4-7m-in-2026/)
- [Why RAG Systems Fail in Production — Part 2: Security, Scale, Data Quality, and Cost](https://medium.com/@abyakod/why-rag-systems-fail-in-production-part-2-security-scale-data-quality-and-cost-999e17dd5b36)
- [The $40 Billion Paradox: Why Enterprise RAG Success Hides Behind Security Walls](https://ragaboutit.com/the-40-billion-paradox-why-enterprise-rag-success-hides-behind-security-walls/)
- [7 RAG Developer Challenges Reddit Reveals in 2026](https://ragaboutit.com/7-rag-developer-challenges-reddit-reveals-in-2026/)
- [7 RAG Failure Modes Crippling Enterprise Deployments in 2026](https://ragaboutit.com/7-rag-failure-modes-crippling-enterprise-deployments-in-2026/)
- [Qdrant Pricing](https://qdrant.tech/pricing/)
- [Langfuse Self-Hosted Pricing](https://langfuse.com/pricing-self-host)
- [Ultimate Guide to AI Engineering Portfolios](https://www.dataexpert.io/blog/ultimate-guide-ai-engineering-portfolios)
- [AI Job Market 2026: A Junior Engineer's Survival Guide](https://www.transparent.tech/ai-job-market-junior-engineer-survival-guide-2026/)
- Upstream: `AI_ENGINEER_PORTFOLIO_PLAN.md` (this folder) — the 12-video synthesis this project was selected from
