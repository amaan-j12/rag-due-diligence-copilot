# AI Engineer Portfolio Plan — Top 5 Projects

Synthesized from 12 analyzed YouTube transcripts (career coaches, ex-Google/Amazon/Microsoft engineers, an AI PhD, and a live 4-hour build) + prior deep research on 2026 hiring data (340% growth in LLM/RAG postings vs. 18% decline in generic ML postings) + India-remote salary reality.

One video (68FcZUpgC7w, Hindi, 8+ hours) turned out to be a generic beginner Python/AI bootcamp ad, not project-specific — excluded from the synthesis below as low-signal.

---

## What all 12 videos agreed on (cross-video consensus)

1. **Tutorial-tier projects are dead.** Chatbot wrappers, ChatGPT clones, basic RAG-follow-alongs — every single video independently called these out as actively harmful to a resume in 2026, not just neutral.
2. **The gap that gets you hired is production maturity, not the demo.** Eval harnesses, observability/tracing, CI regression gating, latency budgets (P50/P95), cost-per-request tracking — these appeared in nearly every video as *the* differentiator, described as "something almost nobody puts in their portfolio."
3. **RAG and multi-agent orchestration (via LangGraph) are the two most-cited skill areas**, consistent with the market data already gathered.
4. **One counter-signal worth taking seriously**: a video about a student ("Viktor") who got hired off one *simple but fully complete and deployed* project — a local voice-transcription cleanup tool — beat candidates with flashier, half-finished multi-agent demos. The lesson: depth + completeness + explainability beats complexity for at least one project in your set.
5. **Salary corroboration**: median AI engineer pay ~$180K in the US, senior roles at OpenAI/Meta $860K–$1.27M — consistent with earlier research; reinforces that the RAG/agents/LLMOps skill cluster is what's actually being paid for.
6. **A recurring roadmap structure** (4 videos independently converge on this): Python + Git fundamentals → LLM API integration + prompt engineering → RAG + agents + LangGraph + LLMOps → portfolio + deployment. ~80% of the actual job is described as "software engineering," ~20% "AI-specific."

---

## The 5 Projects

### 1. Production-grade RAG system with hybrid retrieval + eval harness

**Why:** Cited in nearly every video as the single most common enterprise AI pattern — but almost everyone stops at "basic demo" RAG. The gap between demo-RAG and production-RAG is explicitly called out as the highest-leverage place to differentiate.

**Stack:** LlamaIndex (orchestration) + Qdrant (vector store) + BM25 hybrid search + a cross-encoder reranker (sentence-transformers) + RAGAS (evaluation).

**Scope (1.5 weeks):** Pick one real, retrieval-difficult corpus (legal filings from SEC EDGAR, or PubMed abstracts). Build ingestion → chunking (500-800 tokens, ~100 overlap) → hybrid retrieval → reranking → citation-enforced generation (system explicitly refuses to answer when retrieved chunks don't support a claim).

**Architecture:** Document ingest → chunker → embeddings → Qdrant + BM25 dual index → cross-encoder reranker (top-20 → top-5) → prompt template (versioned in a config file, not hardcoded) → LLM generation with mandatory citations → RAGAS eval script wired to run on every change.

**Output/deliverable:** Deployed Streamlit/Gradio demo + a written eval report showing faithfulness, answer relevancy, and context precision/recall scores, with a before/after comparison once hybrid search + reranking are added over naive vector-only search.

**What impresses a recruiter specifically:** The before/after eval numbers. Almost no junior portfolio has quantified the *improvement* from adding hybrid retrieval — that single chart is the evidence of engineering judgment, not tutorial-following.

---

### 2. Robust multi-agent research system (LangGraph)

**Why:** Called the most fun *and* most commonly broken category — multiple videos describe the same failure mode: wire up 2-3 agents, demo looks great, then one bad output cascades and the whole system collapses on a harder task. Building specifically *for* robustness (not just wiring agents together) is the differentiator.

**Stack:** LangGraph (explicit state machine, not implicit chains) + Pydantic (structured validation between agent handoffs) + a search/fetch tool (SerpAPI or similar).

**Scope (1.5 weeks):** A competitive-research tool: give it a company name, it researches the company, competitors, and market positioning, and produces a structured briefing.

**Architecture:** Planner agent (breaks the request into subtasks, validates researcher output and retries with a revised query if the result is off-topic or empty) → researcher agent (web search + URL fetch) → synthesizer agent (writes the final report). Full tracing on every agent's inputs/outputs/tool calls (Langfuse, self-hosted).

**Output/deliverable:** Deployed demo + a short "failure mode" writeup: deliberately break it (bad search results, malformed tool output) and show how the planner's validation loop catches and recovers, rather than silently cascading garbage downstream.

**What impresses a recruiter specifically:** The explicit retry/validation loop and the "I broke it on purpose and it recovered" narrative — this maps directly to the exact interview questions one video described being asked at a real $15M-funded agentic startup (crash resumption, context management, agent evaluation).

---

### 3. Privacy-constrained local/offline AI app

**Why:** Independently flagged in three videos as a near-zero-competition niche — most candidates have zero hands-on experience with the real constraints (HIPAA, no-external-API industries, edge deployment) that make this a genuine hiring requirement at specific companies, not a nice-to-have.

**Stack:** Ollama (local inference) + a 3-7B model class (Llama 3.2, Gemma, Mistral 7B, Qwen 3) + Pydantic (schema-enforced structured output) + no cloud APIs at all, by design.

**Scope (1 week):** Pick one domain where the privacy constraint is real (clinical note → structured data extraction is the most concrete example given across videos). Build it as offline-only from day one, not offline-as-an-afterthought.

**Architecture:** Local model via Ollama → enforced JSON output schema validated with Pydantic → automatic retry-with-reprompt on invalid output → a benchmarking layer comparing 3 candidate local models (tokens/sec, memory, output quality on 30-50 standardized test prompts) → optional quantization (GGUF Q4/Q5) trade-off documentation.

**Output/deliverable:** A technical comparison report with real numbers (not vibes) plus the working local tool. This report *is* the deliverable — treat it like a mini technical decision doc a real team would produce when choosing a model.

**What impresses a recruiter specifically:** Systematic model comparison with real benchmark numbers is described in one video as "the most impressive deliverable of the entire project" — it signals you think about model selection as an engineering decision, not a popularity contest.

---

### 4. End-to-end deployed AI product (no framework — raw SDK)

**Why:** Directly modeled on the one concrete "this got someone hired" story across all 12 videos — a simple, complete, explainable, *deployed* project beat flashier half-finished ones. Also mirrors a full real 4-hour build video that used raw OpenAI SDK + Postgres + Docker + Render with zero agent framework, deliberately.

**Stack:** Raw OpenAI/Anthropic SDK (no LangChain/LangGraph — the deliberate variation, proving you understand what the frameworks abstract away) + FastAPI + Postgres + Docker + a scheduled job (cron) + email delivery (Resend/SMTP), deployed on Render or Fly.io.

**Scope (1.5 weeks):** A personalized daily AI digest: scrapes a small set of RSS feeds / YouTube channels / blogs you actually care about, uses an LLM to filter and summarize based on stated interests, and emails you a digest every morning.

**Architecture:** Scheduled scraper → Postgres storage → LLM personalization/summarization pass → templated email → cron-triggered send, all Dockerized with proper env-var config, logging, and a public landing page explaining what it does.

**Output/deliverable:** A live product you and others can actually subscribe to, with a public repo and a landing page — not just a GitHub README.

**What impresses a recruiter specifically:** "Tell me about a project you built" gets a one-sentence answer anyone immediately understands, then you can go arbitrarily deep on the architecture when asked. That legibility-plus-depth combination is exactly what the hired-student story credits.

---

### 5. Fine-tuning + eval capstone (ties projects 1-4 together)

**Why:** Fine-tuning is explicitly framed across videos as *not* the default move — only worth it with a clear, measurable gap prompting can't close. Framed as a capstone here specifically because it forces you to produce the one artifact almost nobody has: a rigorous before/after comparison, and because instrumenting Langfuse/observability across all 4 prior projects turns four separate demos into one coherent "AI systems engineer" portfolio narrative.

**Stack:** Hugging Face TRL or Unsloth (LoRA/QLoRA) + a small base model (Qwen 3 8B class) + Langfuse (observability, wired retroactively into projects 1, 2, and 4).

**Scope (1.5 weeks):** Pick a narrow, well-defined task where prompting demonstrably falls short — structured JSON extraction from messy text, or tool-call parameter accuracy (both explicitly recommended). Fine-tune with LoRA on 100-2,000 clean examples, then layer DPO-style preference tuning on top if time allows.

**Architecture:** Baseline measurement (best prompt on held-out test set) → SFT with LoRA → re-evaluate → optional DPO preference pass → re-evaluate again → training curves and before/after metrics written up. Separately: retrofit Langfuse tracing onto projects 1, 2, and 4 so the whole portfolio shares one observability story.

**Output/deliverable:** A written report with the training curve, before/after numbers on JSON validity rate / exact match / refusal correctness, and a single dashboard view (or writeup) showing traces across all 4 other projects — the capstone that makes "I built 4 disconnected demos" read instead as "I built and instrumented a coherent set of production AI systems."

**What impresses a recruiter specifically:** Quantified before/after fine-tuning results plus cross-project observability is the rarest combination in the research — it's the one project explicitly aimed at closing out the portfolio as a system rather than a pile of separate weekend projects.

---

## Timeline (6-8 weeks total, per your target)

| Week | Project |
|---|---|
| 1-1.5 | RAG system + eval harness |
| 2.5-3 | Multi-agent LangGraph system |
| 4 | Local/offline privacy-constrained app |
| 5-5.5 | End-to-end deployed product (raw SDK) |
| 6.5-8 | Fine-tuning capstone + retrofit observability across all 4 |

## Portfolio presentation notes (from the research)

- Every project needs a **written case study**, not just a README: the problem, the architecture, what you tried that failed, the numbers.
- **Deploy everything** — a live link beats a repo every time.
- On your resume: list Python, LangGraph, LlamaIndex, RAG, vector databases, fine-tuning (LoRA), and observability (Langfuse) explicitly — these are the exact keywords job descriptions are matching against per the market-shift data.
- Given India-based remote reality discussed earlier ($1,200-$3,000/month realistic baseline, up to $3,000-$5,000 as an outlier with a strong portfolio): this 5-project set is specifically what pushes you toward the outlier end, not the baseline — referrals still matter more than the portfolio alone for getting the first interview.
