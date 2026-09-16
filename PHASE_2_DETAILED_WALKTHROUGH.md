# Phase 2 — The Complete Walkthrough (build this yourself, step by step)

This document explains **every single thing built in Phase 2**, in the order it was built, with the actual code, why each decision was made, the real bugs hit and how they were diagnosed, worked examples with real numbers, and the actual iteration history — including the mistakes. The goal: after reading this, you should be able to rebuild the faithfulness verifier from scratch, and — just as importantly — recognize the same classes of bugs if you hit them again.

Companion docs: `PHASE_1_DETAILED_WALKTHROUGH.md` (the retrieval/generation/eval pipeline this builds on top of), `PHASE_2_HANDOVER.md` (session-by-session log), `eval/verification_report.md` (the final results writeup), `PHASE_3_HANDOVER.md` (what's next).

---

## The mental model first

Phase 1 built a system that answers questions with citations. But a citation is not proof — an LLM can cite a real chunk right next to a claim that chunk doesn't actually support, and a reader trusts the answer *because* it has a citation, without checking whether the citation really backs the specific sentence next to it. This is a documented, named failure mode in production RAG systems (the research this whole project is built around: ~73% of cited RAG answers contain at least one unsupported claim, and reviewers trust them anyway because of the citation itself — the "citation halo effect").

Phase 2 builds the component that catches this: a **faithfulness verifier**. After the model generates an answer, before it reaches a user, a second, independent process (1) breaks the answer into individual factual claims, and (2) checks each claim against the actual retrieved context — not "is this topically related," but "does the context genuinely say this." Claims that don't check out get flagged.

```
 [generated answer + retrieved context]
              │
              ▼
 ① CLAIM EXTRACTION (break answer into atomic, self-contained facts)
              │
              ▼
 ② ENTAILMENT CHECK (does context support each claim? entailed / not_entailed)
              │
              ▼
 ③ REFUSAL DETECTION (is this even a checkable answer, or a refusal?)
              │
              ▼
 ④ SCORE (fraction of claims entailed → pass/fail badge)
              │
              ▼
 ⑤ SURFACE TO USER (Streamlit badge: verified / flagged claims listed)
```

Steps ① and ② started as two separate LLM calls and were later merged into one (see Step 5 below) — the diagram reflects the logical steps, not the final call count.

---

## Step 0 — The backend decision (a real dead end, worth knowing about)

The original plan was to run verifier LLM calls through **opencode** (a free, agentic coding-CLI tool, similar to Claude Code but pointed at other model providers) to avoid Anthropic API costs entirely. This did not work, for reasons worth understanding before you try it yourself:

1. `opencode auth login` opens an interactive TUI provider picker — not scriptable, needs a human at a real terminal to complete a browser login or paste an API key.
2. The model name baked into this project's earlier code, `opencode/deepseek-v4-flash-free`, **doesn't exist** on OpenCode Zen (the provider that hosts it). It was likely renamed or deprecated between when it was documented and when this session ran.
3. OpenCode Zen itself is not free — it requires signing up and funding a $20 pay-as-you-go balance to get an API key for most models.
4. Zen does have genuinely free models (Big Pickle, MiMo-V2.5 Free, Nemotron variants), but even after switching to one of those, the user hit **opencode's free-tier usage limit** mid-session.

**Lesson: verify a "free" backend's actual current pricing and model availability before architecting around it — documentation and model names go stale, especially for fast-moving free-tier AI services.**

**The fallback that actually worked:** `claude` CLI (already installed, already authenticated, part of the existing Claude Code subscription — no new signup, no funding, no cost tracked as `$0.00` per call in `logs/api_usage.jsonl`). This is the same `_try_claude_cli()` function already built in Phase 1's `src/generation/generate.py`:

```python
def _try_claude_cli(system_prompt: str, user_prompt: str, timeout: int = 90) -> str | None:
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    proc = subprocess.run(
        ["claude", "-p", full_prompt, "--output-format", "text", "--model", "haiku"],
        capture_output=True, text=True, timeout=timeout, cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    _log_usage("claude_cli", 0, 0, 0.0)
    return proc.stdout.strip()
```

Reusing this function (rather than writing a new one) meant the verifier's backend choice was a one-line import swap, not new infrastructure.

---

## Step 1 — First version: two calls, `extract_claims` + `check_entailment`

The first working version of `src/verification/faithfulness_verifier.py` used two separate LLM calls per verification, matching the conceptual RAGAS faithfulness-metric pattern this project's `BUILD_SPEC.md` originally specified:

```python
def extract_claims(answer: str) -> list[str] | None:
    prompt = CLAIM_EXTRACTION_PROMPT.format(answer=answer)
    out = _try_claude_cli("You output only valid JSON.", prompt)
    claims = _parse_json_array(out)
    if not isinstance(claims, list):
        return None
    return [c for c in claims if isinstance(c, str) and c.strip()]


def check_entailment(claims: list[str], context: str) -> list[dict] | None:
    prompt = ENTAILMENT_PROMPT.format(context=context, claims=json.dumps(claims))
    out = _try_claude_cli("You output only valid JSON.", prompt)
    verdicts = _parse_json_array(out)
    if not isinstance(verdicts, list):
        return None
    return verdicts
```

**Why parse with a regex instead of `json.loads()` directly:**

```python
def _parse_json_array(text: str):
    if text is None:
        return None
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
```

Claude CLI output for a "JSON-only" instruction still sometimes wraps the array in a markdown code fence (` ```json\n[...]\n``` `) or adds a leading sentence. `re.search(r"\[.*\]", text, re.DOTALL)` greedily grabs everything between the first `[` and the last `]`, which reliably strips the fence/prose wrapper without needing to special-case every possible wrapping format. This is the same defensive-parsing lesson Phase 1 hit with RAGAS judge JSON truncation — LLM "structured output" is a strong suggestion, not a contract, so parse defensively.

---

## Step 2 — Testing immediately exposed a scoring problem (this is the important part)

Before trusting any pass/fail number, 12 hand-written injected-error test cases were built (`eval/test_verification.py`) — 6 where the answer should PASS (fully supported by context) and 6 where it should FAIL (contains a claim the context contradicts). The first real run:

```
Catch rate (correct PASS/FAIL vs expected): 9/12 (75%)
  [MISMATCH] t05: expected=PASS actual=FAIL
  [MISMATCH] t09: expected=PASS actual=FAIL
  [MISMATCH] t11: expected=PASS actual=FAIL
```

**All three mismatches were the same direction: real, correct answers being wrongly flagged as unfaithful.** Digging into one (`t05` — a Microsoft cybersecurity-risk claim):

```json
{
  "flagged": ["Cyberattacks against Microsoft's cloud services infrastructure could result in service disruption, data breaches, and reputational harm."],
  "reason": "The context describes risks to 'our' datacenters and cloud services but does not identify the organization as Microsoft, so the Microsoft-specific attribution cannot be confirmed from the context alone."
}
```

**Root cause: SEC filings self-refer as "the Company," "we," or "our" — never by name.** The claim named "Microsoft" explicitly (because the generation step's prompt template requires this — see Phase 1's citation rules), but the raw context chunk text never says "Microsoft" literally. The entailment judge had no way to confirm the two referred to the same entity, so it correctly (by a strict reading) marked the claim unsupported — even though it obviously was.

**This bug also existed in the live app path**, not just the test fixtures: `app.py`'s `verify_answer(result["answer"], [c["text"] for c in chunks])` call was passing raw chunk text with no company metadata attached, same gap.

### The fix: thread an `entity` parameter through

```python
def check_entailment(claims: list[str], context: str, entity: str | None = None) -> list[dict] | None:
    if entity:
        context = f"[This context is excerpted from {entity}'s SEC filing.]\n\n{context}"
    ...
```

Populated from wherever entity information is actually available:
- **Test fixtures:** an explicit `"entity": "Microsoft"` field added to each test case.
- **Eval-set audit:** chunk IDs are formatted `TICKER_DATE_hash` (see Phase 1's chunk-ID design) — `item["chunk_ids"][0].split("_")[0]` recovers the ticker for free, no new data needed.
- **Live app:** only attached when *every* retrieved chunk shares one ticker — a cross-company comparison question shouldn't get a single (possibly wrong) entity hint attached to mixed-company context:
```python
tickers = {c["ticker"] for c in chunks}
entity = chunks[0]["company"] if len(tickers) == 1 else None
```

**Lesson: when a verification/judge step operates on text stripped of the metadata a human reader would take for granted (like "which company is this about"), check whether that metadata gap is a synthetic-test artifact or a real production gap. Here it was both — fixing it once, at the shared function, fixed the live app too.**

---

## Step 3 — The prompt tuning that actually mattered: paraphrase tolerance

A second, related fix. Even after the entity fix, catch rate hovered at 58-75% across runs, with the *same* asymmetric pattern: PASS cases wrongly flagged, FAIL cases always caught correctly (6/6, every single run). This asymmetry itself was a useful diagnostic — it meant the judge wasn't randomly wrong, it was **systematically too strict** about wording.

```python
ENTAILMENT_PROMPT = """...
Mark a claim "entailed" if the context supports it -- including reasonable
paraphrases, summaries, or rewordings of what the context says. You do not
need a verbatim or near-verbatim match; if a person reading the context
would agree the claim is a fair restatement of it, that counts as entailed.

Mark a claim "not_entailed" ONLY if one of these is true:
  (a) the context contradicts the claim, or
  (b) the context is silent on the claim's subject entirely.
Do not mark a claim not_entailed just because it adds mild interpretation,
mild severity language (e.g. "significant", "key"), or restates the same
idea in different words -- that is normal summarization, not an unsupported
claim.
..."""
```

After this, catch rate jumped to 5/6 correct on the PASS-only subset (validated cheaply — 6 calls, not a full 24-call rerun — before spending budget on a full re-test). **Lesson: when debugging an LLM judge's accuracy, isolate a cheap subset to validate a prompt change before re-running the expensive full suite.**

---

## Step 4 — A subtler false positive: generic boilerplate has no entity signal

One test case (`t11`) kept failing even after both fixes above:

```
ANSWER: "Apple states it is currently subject to various legal proceedings..."
CONTEXT: "The Company is subject to various legal proceedings and claims
          that arise in the ordinary course of business."
```

This context is *generic litigation boilerplate* — nearly every 10-K in the corpus has a near-identical sentence. Nothing in the context text itself distinguishes it as Apple's filing versus anyone else's, so even with the `entity="Apple"` hint attached, the judge sometimes (non-deterministically — `claude -p` exposes no temperature control) flagged it. Re-checking the same input in isolation sometimes got it right, sometimes not.

**This was accepted as inherent judge variance, not chased further** — 11/12 (92%) catch rate on the final run cleared the ≥80% bar, and a single boilerplate edge case flipping occasionally is a known, disclosed limitation rather than a systematic defect. Not every imperfection needs to be engineered away; some are worth documenting and moving on from, especially when the cost of further tuning outweighs the value (see `eval/verification_report.md` for exactly how this was reported).

---

## Step 5 — Two real infrastructure bugs, found by actually running the batches

### Bug 1: resume logic silently credited failed calls as "correct"

The batch runner (`eval/run_verification_audit.py`) was built to be resumable — write results after every item, and on re-run, skip items already present in the output file:

```python
# THE BUG (first version):
done_ids = {r["id"] for r in results}   # includes errored items!
```

An item whose LLM call failed got `pass=False` as its error-path default. For a test case whose *expected* label happened to be `FAIL`, `pass=False` accidentally matched — and the scoring code marked it `"correct": True`, because it only compared `pass` against `expected`, never checking whether the call had actually succeeded. **This is a genuine false-confidence bug: a batch could show a high catch rate while several of its "correct" results were actually silent failures that happened to land on the right side by coincidence.**

This surfaced concretely: a batch run showed `9/12 call failures` in the raw log, yet the catch-rate summary still reported passing marks for several of those same failed items — the two numbers didn't reconcile, which is what caught it.

**The fix:**

```python
by_id = {r["id"]: r for r in existing if not r.get("error")}
```

Only genuinely successful results count as "done." Errored items are excluded from the skip-set, so a re-run automatically retries only what actually failed — no wasted calls re-verifying items that already succeeded, no permanent skip of items that never really ran.

**Lesson: when a pipeline step has a fallback/default value on failure, audit every place that default could accidentally satisfy a downstream check. `pass=False` as an error default is reasonable in isolation; `pass=False` silently equaling `expected=FAIL` in a scoring comparison is a landmine.**

### Bug 2: transient CLI failures under tight sequential load

Running 12-24 `claude -p` subprocess calls back-to-back in a loop produced occasional failures (`claim extraction failed`) — but the *exact same call*, re-run in isolation seconds later, succeeded every time. This ruled out a prompt/logic bug (isolated calls proved the prompt was fine) and pointed at something in how the CLI behaves under rapid repeated subprocess spawning.

**The fix — general-purpose retry, not root-cause elimination** (the actual cause wasn't fully diagnosed — no error message is exposed by the CLI's exception-swallowing `except Exception: return None`):

```python
def _call_with_retry(system: str, prompt: str, attempts: int = 2, backoff_s: float = 3.0) -> str | None:
    for i in range(attempts):
        out = _try_claude_cli(system, prompt)
        if out:
            return out
        if i < attempts - 1:
            time.sleep(backoff_s)
    return None
```

Plus 1.5s spacing between items in the batch loop. Result: 0 unrecovered call failures across 90+ total verification calls in the final run. **Lesson: not every transient failure needs (or can be given, with the tools at hand) a root-cause fix — a retry-with-backoff is a legitimate, honest mitigation when the failure is confirmed transient (isolated retry succeeds) rather than logical (same input, same wrong output every time).**

---

## Step 6 — Latency: merging two calls into one

The first fully-working version (two calls per verification: extract, then check) measured at **~96-104 seconds per verification** on real generated answers — far past the original `<5s` target set in `PHASE_2_CHECKLIST.md` (a target that implicitly assumed an API-based judge, not a CLI-subprocess one).

**The fix:** merge claim extraction and entailment checking into one combined prompt, one subprocess call:

```python
COMBINED_PROMPT = """You will be given an ANSWER and a CONTEXT (retrieved
document excerpts). Do this in two steps, but only output the result of
step 2.

STEP 1 (internal): Break the ANSWER into self-contained factual claims. ...
STEP 2 (output this): For each claim from step 1, decide "entailed" or
"not_entailed" against the CONTEXT. ...

Output ONLY a JSON array of objects, one per claim... If the ANSWER has no
checkable factual claims, output [].
"""
```

**Result — verified before trusting it:** re-ran the 12-item test batch to confirm the merge didn't cost accuracy (it didn't — 92% catch rate held, identical to the two-call version), then measured latency: **median 58.8s, mean 68.0s** — roughly halved, matching the expectation of removing one subprocess-spawn round trip. Not the original `<5s`, but a real, disclosed improvement, and the honest remaining gap (CLI-subprocess overhead + full-context prompt processing time) is now attributable to a specific, named cause rather than an unexplained number.

**Latency scales with answer length, not a fixed constant:** a short 1-claim test answer verified in 16s; a long 33-claim real answer took 74s; the slowest single item in the full audit took 180.8s. Report a distribution (median/mean/range), not a single average — a single number here would hide that the system has a real, answer-dependent latency tail.

---

## Step 7 — Refusal detection: a second false-positive lesson

30 real eval questions include 6 deliberately out-of-scope ones (weather, stock-buy advice, a cookie recipe). The generation model correctly refuses these ("This system only answers questions grounded in the indexed SEC filings"). But a refusal is a **meta-statement about the system's own scope** — there's no document claim to check it against, so entailment verification against irrelevant retrieved context was, predictably, marking every refusal as unfaithful. This is a *category* error (checking the wrong thing), not an accuracy problem in the checker itself.

**First attempt — too generic, caused a real false positive:**

```python
_REFUSAL_PATTERNS = [
    "outside the scope",   # BAD -- too generic
    "out of scope",        # BAD -- too generic
    ...
]
```

This matched a genuinely substantive answer about NVIDIA's export-control risk: *"...chips **outside the scope** of the controls..."* — a real document fact, wrongly skipped as if it were a refusal.

**The fix — patterns built from actual observed refusal text, not guessed:**

```python
_REFUSAL_PATTERNS = [
    "i can't answer this",
    "i can't answer that",
    "i don't have enough information in the retrieved filings",
    "this system only answers questions grounded in",
    "does not provide investment advice",
    "does not provide personal investment advice",
    "does not provide guidance on",
    "cannot provide guidance on",
]
```

Every pattern requires self-referential "I"/"this system" framing — phrasing that cannot plausibly appear inside genuine financial-document analysis. After rebuilding: **6/6 out-of-scope questions correctly skipped, 0 false positives** across the rest of the 24 in-scope questions.

**Lesson: for any keyword-based heuristic meant to catch a specific *kind* of text (here: a refusal, which is self-referential by nature), build the pattern list from actual samples of the real thing, not from generic words that happen to describe the concept. "Scope" is a word that appears constantly in legitimate regulatory/legal text — "this system only answers questions" does not.**

### An honest remaining limitation: mixed real+refusal answers

3 in-scope *comparison* questions (e.g. "compare J&J's and BMS's litigation risk") retrieve filings for one company but not the other — the model correctly answers substantively for the found company while refusing for the missing one, **in the same response**. `is_refusal()` is binary (skip the whole answer or verify the whole answer), so these get entirely skipped, including the real, checkable content. This under-verifies rather than over-flags — the safer failure direction — but it's disclosed as a Phase 3 item (segment mixed answers by company before verification) rather than silently accepted as fine.

---

## Step 8 — The final `verify_answer()` contract

```python
def verify_answer(answer: str, contexts: list[str], entity: str | None = None) -> dict:
    """
    Returns:
        {
            "pass": bool,             # score >= 0.8, or True if skipped (refusal)
            "score": float | None,    # fraction of claims entailed; None if skipped
            "flagged_claims": list[str],
            "total_claims": int,
            "latency_ms": float,
            "skipped": bool,          # True if answer was a detected refusal
            "error": str | None,      # set if the LLM call failed
        }
    """
```

Three distinct outcomes, not two — this matters for callers:
1. **Verified, pass/fail with a real score** — the normal case.
2. **Skipped (`skipped=True`, `score=None`)** — a detected refusal; document-entailment checking doesn't apply. Both `app.py` and `eval/run_verification_audit.py`'s summary logic have to explicitly handle `score=None` (a naive `f"{score:.0%}"` format crashes on `None` — this was caught before shipping by tracing through what the UI code does with a skipped result, not by a runtime crash).
3. **Error (`error` set)** — the LLM call itself failed even after retry. Distinct from "verified and failed" (`pass=False` with a real score) — collapsing these would hide genuine infrastructure failures inside what looks like a real faithfulness judgment.

---

## Step 9 — Wiring into the live app (`app.py`)

```python
with st.spinner("Verifying answer faithfulness..."):
    try:
        tickers = {c["ticker"] for c in chunks}
        entity = chunks[0]["company"] if len(tickers) == 1 else None
        verification = verify_answer(result["answer"], [c["text"] for c in chunks], entity=entity)
    except Exception as e:
        verification = {"pass": None, "error": str(e)}

if verification.get("error"):
    st.info(f"Faithfulness verification unavailable (claude CLI call failed: {verification['error']})")
elif verification.get("skipped"):
    st.info("Answer looks like a refusal/out-of-scope response — faithfulness check not applicable.")
elif verification["pass"]:
    st.success(f"Faithfulness verified ({verification['score']:.0%} of claims supported)")
else:
    st.warning(f"Some claims not fully supported ({verification['score']:.0%} verified). Review citations carefully.")
    if verification["flagged_claims"]:
        st.caption("Flagged claims:")
        for claim in verification["flagged_claims"]:
            st.caption(f"- {claim}")
```

**Why wrap the whole call in `try/except` at the UI layer, not just trust `verify_answer()`'s internal error handling:** `verify_answer()` catches LLM-call failures internally and returns a structured `error` field — but if the CLI binary itself is missing, or an unexpected exception occurs somewhere in the chain (e.g. `chunks` is empty and `chunks[0]` raises `IndexError`), the UI should still degrade gracefully (show the answer, note verification is unavailable) rather than crash the whole page. **This mirrors Phase 1's "fail loud in the pipeline, fail soft in the UI" split** — a pipeline script should raise loudly so a bad run doesn't silently produce corrupted data, but a live user-facing page should never crash outright over a non-critical enhancement feature.

---

## Step 10 — The batch runner, and why resumability was worth building

`eval/run_verification_audit.py` runs verification over either the 12 test fixtures or the 30 real eval questions, in a caller-specified `--start`/`--end` slice, writing results to `--out` **after every single item**, not just at the end:

```python
def commit(item_id: str, record: dict):
    by_id[item_id] = record
    save(out_path, list(by_id.values()))   # persist after every item
```

Real eval-set batches took 5-20 minutes depending on answer length and were run in the background (this session's tooling supports launching a long shell command as a background task and being notified on completion). A batch that ran long enough to exceed a foreground timeout was automatically moved to background mid-run without losing any progress — because every completed item was already durably written to disk, not held in memory until the end. **This is the same principle as Phase 1's "save raw generation records separately from RAGAS scores" — never make an expensive, slow step's output all-or-nothing when it doesn't have to be.**

### Try it yourself

```bash
# 12 injected-error test cases (fast, ~2-4 minutes)
python eval/run_verification_audit.py --source test_cases \
    --start 0 --end 12 --out eval/results/verification_batch_tests.json

# Real eval-set audit, in batches of 10 (each ~5-20 min depending on answer length)
python eval/run_verification_audit.py --source eval_set \
    --start 0 --end 10 --out eval/results/verification_batch_1.json
python eval/run_verification_audit.py --source eval_set \
    --start 10 --end 20 --out eval/results/verification_batch_2.json
python eval/run_verification_audit.py --source eval_set \
    --start 20 --end 30 --out eval/results/verification_batch_3.json
```

If any batch is interrupted (Ctrl-C, crash, or it just runs long), re-running the **exact same command** picks up where it left off — already-verified items are skipped, only what's missing gets (re-)run.

---

## Final results (see `eval/verification_report.md` for the full breakdown)

| Metric | Value |
|---|---|
| Injected-error catch rate | 11/12 (92%) |
| False negative rate on injected errors | 0/6 (0%) — every FAIL case caught |
| Out-of-scope refusals correctly skipped | 6/6 (100%) |
| In-scope, fully-verified pass rate | 19/21 (90%) |
| Avg faithfulness score (fully-verified) | 0.939 |
| Latency | median 58.8s, mean 68.0s, range 16.2s-180.8s |
| Call failures | 0, after retry+backoff fix |

---

## Recap — the full command sequence to build this from scratch

```bash
# 0. (assumes Phase 1 fully built: retriever, generate.py, records_hybrid.json exist)

# 1. Write the verifier core
#    src/verification/faithfulness_verifier.py — combined claim-extraction +
#    entailment prompt, is_refusal() pre-check, verify_answer() entry point

# 2. Write hand-labeled test fixtures
#    eval/test_verification.py — 12 cases, 6 PASS / 6 FAIL, each with an
#    explicit "entity" field

# 3. Write the resumable batch runner
python eval/run_verification_audit.py --source test_cases --start 0 --end 12 \
    --out eval/results/verification_batch_tests.json
# Check catch rate >= 80% before spending calls on the real eval set

# 4. Run the real eval-set audit, batched
python eval/run_verification_audit.py --source eval_set --start 0 --end 10 \
    --out eval/results/verification_batch_1.json
python eval/run_verification_audit.py --source eval_set --start 10 --end 20 \
    --out eval/results/verification_batch_2.json
python eval/run_verification_audit.py --source eval_set --start 20 --end 30 \
    --out eval/results/verification_batch_3.json

# 5. Wire into the live app
#    app.py — call verify_answer() after generate_answer(), show a badge

# 6. Compile the final report
#    eval/verification_report.md — segment results by category (in-scope
#    vs out-of-scope), report latency as a distribution not one number,
#    document every bug found and how it was fixed
```

Every step above has been run and verified against this exact 30-question eval set, generated by the Phase 1 hybrid-retrieval pipeline. See `PHASE_3_HANDOVER.md` for what's next.
