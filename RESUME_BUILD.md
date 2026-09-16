# Resume Build — Phase 1, in progress (session 3)

**For a fresh Claude session with zero memory of the prior build.** Read `BUILD_SPEC.md` first for full architectural context. This doc covers exactly where things stopped and the *specific, already-decided* plan for finishing Phase 1 — do not re-litigate the CLI-vs-API or model-tier decisions below, they were made deliberately after real evidence from a failed run. Just execute.

---

## What's actually done and verified (don't redo this)

| Step | Status | Evidence |
|---|---|---|
| 1. Clean + chunk 90 filings | ✅ Done | `data/chunks.jsonl` — 10,027 chunks |
| 2. Embed + index into Qdrant | ✅ Done | `data/qdrant_db/` |
| 3. BM25 keyword index | ✅ Done | `data/bm25_index.pkl` |
| 4. RRF fusion | ✅ Code written | `src/retrieval/retriever.py` |
| 5. Cross-encoder reranker | ✅ Code written | `src/retrieval/retriever.py` (`mode="rerank"`) |
| 6. Versioned prompt template | ✅ Done | `config/prompt_templates.yaml` |
| 7. Generation (CLI-first, API-fallback) | ✅ Code done, needs a small edit — see below | `src/generation/generate.py` |
| 8. 30 eval questions | ✅ Done | `eval/questions.jsonl` |
| 9. **Naive-pass generation (30/30 questions)** | ✅ **Done and saved** | `eval/results/records_naive.json` — all 30 answers generated via `claude_cli`, zero errors, 473.7s wall time. **Do not regenerate these — reuse this file.** |
| 9b. Naive-pass RAGAS scoring | ❌ Not done — killed mid-run, see "what broke" below | `eval/results/ragas_summary_naive.json` does not exist yet |
| 9c. Hybrid pass, rerank pass | ❌ Not started at all | |
| 10. Before/after chart | ❌ Blocked on the above | `eval/plot_results.py` exists, untested |
| 11. Streamlit app boot check | ❌ Not verified | `app.py` exists, untested |

Nothing about corpus/chunking/indexing/retrieval needs touching. This session is purely about finishing generation + RAGAS scoring + chart + app check.

---

## The decided plan (do this, don't re-derive it)

This was worked out carefully across a long conversation with the user, against a **hard constraint: only $2.60 left on the Anthropic API key** (in `.env.local`, see below). Here is the reasoning, compressed:

1. **Generation → Claude CLI, explicitly pinned to Haiku** (`--model haiku`). Free (part of the Claude Code session, not the API key). Already proven reliable — the naive pass's 30 generation calls all succeeded via CLI with zero errors in 473.7s. No reason to spend API money on generation.

2. **RAGAS judge scoring → Anthropic API, Haiku model.** This is the important one: **do not use the CLI for judge scoring.** We tried it and it broke — see "what broke" below. RAGAS fires many judge calls *concurrently*, and concurrent `claude -p` subprocesses timed out and returned unparseable output. The API handles concurrency fine and Haiku is well-suited to the judge's actual task (structured claim extraction + verification, not open-ended reasoning).

3. **Budget-conditional upgrade**: only if real spend after the Haiku-judged naive + hybrid passes leaves comfortable headroom in the $2.60, re-run **just the rerank pass's judge scoring** on Sonnet 5 (`claude-sonnet-5`, currently intro-priced $2/$10 per M tokens through 2026-08-31) as a one-pass credibility upgrade — the rerank config is the "hero" number most likely to be scrutinized. Do **not** default to this; check `logs/api_usage.jsonl` first and only do it if there's clearly room. Estimated cost for Sonnet-judging one pass alone: roughly $0.4–0.7. Do not do this for all three passes — that was estimated at ~$2–2.9 total, which is too risky against $2.60.

Cost estimate for the recommended default path (Haiku judge, all 3 passes, generation free via CLI): **~$0.6–1.00 total**. Track actual spend as you go, don't trust the estimate blindly.

---

## Exact point of failure this session (read before touching run_ragas.py / generate.py)

Running `eval/run_ragas.py --mode naive` end-to-end via CLI-only (both generation and judge through the `claude` CLI):

- **Generation phase succeeded completely** — 30/30, saved to `eval/results/records_naive.json`, 473.7s, source `claude_cli` for all.
- **RAGAS judge phase failed under concurrency.** From `logs/eval_naive_pipeline.log`: RAGAS's `evaluate()` dispatches judge sub-calls concurrently (progress bar showed 120 tasks for the naive pass — 4 metrics × 30 questions). Within the first ~4 minutes: multiple `TimeoutError` exceptions, multiple `"The LLM did not return a valid classification"` warnings (meaning the CLI subprocess output wasn't parseable JSON), and only 19/120 tasks completed. This is a **concurrency reliability problem specific to spawning many simultaneous `claude -p` subprocesses**, not a usage-limit problem this time (usage limit had already reset earlier in the session, confirmed working).
- The process was killed intentionally (`pkill -f "eval/run_ragas.py"` + `pkill -f "claude -p"`) once the user decided to switch to API-based judging instead of debugging CLI concurrency.
- **Zero dollars spent so far** — confirmed via `logs/api_usage.jsonl`: 104 entries, all `source: claude_cli`, `cost_usd: 0.0` for all. The Anthropic API has not been touched yet this session.

---

## Code changes needed before running anything (not yet made — do these first)

### 1. `src/generation/generate.py`

- `_try_claude_cli()` currently calls `["claude", "-p", full_prompt, "--output-format", "text"]` with no `--model` flag, so it uses the CLI's default model. Add `--model haiku` to this call (per the decided plan, generation should be explicitly pinned to Haiku via CLI, not left on the default).
- `_try_anthropic_api()` currently hardcodes `model=HAIKU_MODEL` — this needs to become a parameter, e.g. `_try_anthropic_api(system_prompt, user_prompt, model=HAIKU_MODEL)`, so the RAGAS judge (in `run_ragas.py`) can request `claude-sonnet-5` specifically for the optional Sonnet upgrade pass, while `generate_answer()`'s own fallback path keeps using Haiku.
- Add Sonnet pricing constants for cost logging when the judge uses Sonnet: `SONNET_MODEL = "claude-sonnet-5"`, `SONNET_PRICE_IN_PER_M = 2.00`, `SONNET_PRICE_OUT_PER_M = 10.00` (intro pricing, valid through 2026-08-31 — check the date; if past that, it's $3.00/$15.00, update accordingly). `_log_usage()` / cost calc inside `_try_anthropic_api()` needs to use the right price table for whichever model was actually called.

### 2. `eval/run_ragas.py`

- `ClaudeJudgeLLM._generate()` currently tries `_try_claude_cli()` first, falling back to `_try_anthropic_api()`. **Change this to skip the CLI entirely and call `_try_anthropic_api()` directly** — per the evidence above, CLI is unreliable for judge scoring under RAGAS's concurrency. Default to `model=HAIKU_MODEL`.
- Add a way to override the judge model per-run (e.g. an optional `--judge-model` CLI arg on `run_ragas.py`, or a parameter threaded into `score_with_ragas()`) so the rerank pass can be re-scored with `model=SONNET_MODEL` later without duplicating code.
- `run_pipeline()` calls `generate_answer(q["question"], chunks)` — this already goes through the CLI-first path in `generate.py`, so once `_try_claude_cli` is pinned to `--model haiku` (change #1 above), no further change needed here. **But**: since `eval/results/records_naive.json` already has valid, complete naive-pass generations, consider adding a `--skip-generation-if-exists` style guard (or just manually short-circuit) so re-running `--mode naive` doesn't burn CLI calls regenerating identical answers. Not strictly required — CLI is free — but avoids ~8 minutes of redundant wall-clock time. Simplest approach: if `eval/results/records_naive.json` exists and has 30 records, load it directly and skip straight to `score_with_ragas()` for the naive pass specifically.

---

## How to resume, step by step

1. **Set the API key from `.env.local`** (already saved there by the user — a bare key string, no `KEY=` prefix, no trailing newline). Shell state does not persist between separate tool calls, so export it inline in every command that needs it:
   ```bash
   cd "/Users/mohammadamaan/Claude Projects/RAG Due Diligence Copilot"
   export ANTHROPIC_API_KEY="$(cat .env.local | tr -d '\n')"
   source venv/bin/activate
   ```

2. **Make the code changes above** in `generate.py` and `run_ragas.py` before running anything.

3. **Run the naive pass** (reusing existing generation records if you added the short-circuit, otherwise regenerating via CLI/Haiku — either is fine, just note which):
   ```bash
   export ANTHROPIC_API_KEY="$(cat .env.local | tr -d '\n')" && python eval/run_ragas.py --mode naive
   ```
   Check `eval/results/ragas_summary_naive.json` gets created. Check real cost:
   ```bash
   cat logs/api_usage.jsonl | python3 -c "import json,sys; print(f'\${sum(json.loads(l)[\"cost_usd\"] for l in sys.stdin):.4f}')"
   ```

4. **Run the hybrid pass**, same pattern:
   ```bash
   export ANTHROPIC_API_KEY="$(cat .env.local | tr -d '\n')" && python eval/run_ragas.py --mode hybrid
   ```
   Check spend again. If already above ~$1.50, stay on Haiku for the judge on the rerank pass too — don't risk the Sonnet upgrade.

5. **Run the rerank pass**:
   ```bash
   export ANTHROPIC_API_KEY="$(cat .env.local | tr -d '\n')" && python eval/run_ragas.py --mode rerank
   ```

6. **Only if there's clearly comfortable budget left** (i.e. total spend so far is well under $2.00), consider re-scoring just the rerank pass's judge with Sonnet as the credibility upgrade described above. Otherwise skip this — Haiku-judged numbers across all three passes is a perfectly legitimate, complete result.

7. **Generate the chart**:
   ```bash
   python eval/plot_results.py
   ```
   Verify `eval/results/before_after_ragas.png` looks sane — naive should generally score lower than hybrid, hybrid+rerank should generally be highest (this is the whole point of the project; if the numbers come out flat or inverted, don't just accept it — sanity check the RAGAS scoring code and the retrieval modes before declaring done).

8. **Verify the Streamlit app boots**:
   ```bash
   streamlit run app.py
   ```
   Load it, run one query, confirm it doesn't crash, then Ctrl+C. Don't leave it running.

9. **Write `PHASE_2_HANDOVER.md`** once all of the above has real numbers — include the actual RAGAS scores, actual total cost from `logs/api_usage.jsonl`, and carry forward the "what broke" section below plus anything new.

---

## What broke and what was learned (keep for the case study)

- `ragas==0.4.3` conflicted with `langchain-community` — pinned to `ragas==0.2.15` (see `requirements.txt`).
- System Python 3.14 too new for ML wheels — venv rebuilt on Python 3.11.
- SEC "Item" header regex initially missed titles with trailing periods / long titles — loosened.
- Financial-services 10-Ks section less cleanly than tech filings (one JPM section absorbed ~985K chars) — chunking still worked underneath via the token sub-splitter, section metadata is just coarse for these filings. Known limitation, not fixed.
- Confirmed cross-sector ambiguity directly: BM25-only search for "Apple risk factors supply chain" ranked Eli Lilly/NVIDIA/Pfizer above Apple — real evidence for why hybrid+rerank matters. Still needs final verification against the actual hybrid/rerank RAGAS numbers once step 4/5 above run.
- **Claude Code CLI session usage limits are a real constraint for high-volume CLI pipelines** — hit this in an earlier session (100+ calls, limit reset ~11pm IST). The API fallback exists for this reason.
- **New this session: the CLI is also unreliable for RAGAS's concurrent judge calls specifically**, independent of usage limits — see "Exact point of failure" above. Timeouts + invalid JSON parses when many `claude -p` subprocesses run at once. This is why judge scoring moved to the API. Generation (sequential, one call at a time in `run_pipeline()`) does not have this problem.
- Budget reality: user has **$2.60 total** on the Anthropic API key for this entire remaining eval. Sonnet-judging all three passes was estimated at $2–2.9 — too risky, hence the Haiku-default / Sonnet-only-if-headroom plan above. Track `logs/api_usage.jsonl` running total after every single pass, not just at the end.

---

## Budget/time reality check

- Generation: free (CLI/Haiku), ~8 min/pass × 3 passes ≈ 25 min total, already 1/3 done (naive).
- Judge scoring: API/Haiku, ~$0.6-1.00 total estimated for all 3 passes, real number could vary — check after each pass. Time: should be much faster than the CLI attempt since the API handles RAGAS's concurrency natively (no more timeouts) — expect single-digit minutes per pass, not the 4+ minutes for only 19/120 tasks seen with CLI.
- Chart + Streamlit check: trivial, a few minutes.
