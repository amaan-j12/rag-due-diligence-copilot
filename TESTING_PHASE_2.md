# Phase 2 Testing — Run via opencode (manual, batched)

**Why you're running this instead of Claude:** these are the "long-CLI" parts
of Phase 2 — many sequential LLM judge calls (2 per item: claim extraction +
entailment check). All the code (`src/verification/faithfulness_verifier.py`,
`eval/test_verification.py`, `eval/run_verification_audit.py`) is already
written and reviewed. opencode's only job here is to **execute it**, batch by
batch, and write results to `eval/results/`. It should not write new code or
change the scripts.

---

## 0. One-time setup (do this first)

```bash
opencode auth login
```
Pick a provider (e.g. OpenCode Zen → a free model like Big Pickle or
MiMo-V2.5 Free). Confirm it worked:
```bash
opencode auth list
```
Should show ≥1 credential.

**Find your model string.** The scripts default to `opencode/big-pickle`.
If you authenticated with a different model, pass `--model <your-model>` in
every command below (e.g. `--model opencode/mimo-v2.5-free`).

---

## 0.5 Full task briefing (paste ONCE — opencode runs the whole thing itself)

Since your opencode app is a full agentic session (file + bash access, like
this Claude Code session), you don't need to feed it commands one at a
time. Paste the whole briefing below and let it drive itself through all 4
batches, checkpointing after batch A.

```
You're working inside the project at the current directory (RAG Due
Diligence Copilot). You have file and bash access here — use them directly,
do not ask me to run commands manually.

PROJECT CONTEXT: This is a RAG (retrieval-augmented generation) due-diligence
assistant over 90 SEC EDGAR filings, 30 companies. Phase 1 (already done)
built retrieval, generation, and RAGAS evaluation. Phase 2 (in progress)
adds a "faithfulness verifier": a component that checks whether a generated
answer's claims are actually supported by the retrieved document context it
cites — catching hallucinated or unsupported claims before they reach a
user.

WHAT'S ALREADY BUILT (Claude Code wrote and reviewed this — do not modify
any of it):
- src/verification/faithfulness_verifier.py — the verifier itself. Two LLM
  calls per item: (1) decompose the answer into atomic claims via
  extract_claims(), (2) check whether the context entails each claim via
  check_entailment(). Both calls route through YOU, via the
  _try_opencode_cli() function it imports from src/generation/generate.py.
  That's why this task needs to run inside an opencode session specifically.
- eval/test_verification.py — 12 hand-written test cases with known
  expected PASS/FAIL outcomes (6 should pass verification, 6 contain a
  deliberately wrong/contradicted claim and should fail it). Used to
  measure whether the verifier actually catches errors.
- eval/run_verification_audit.py — the runner. Takes --source
  (test_cases | eval_set), --start/--end (row range, for batching),
  --model, and --out (JSON output path). Prints a summary at the end
  (catch rate for test_cases, pass rate/avg score/avg latency for
  eval_set).

YOUR TASK: Run these 4 commands IN ORDER from the project root. Each is a
separate batch of ~10-12 items (~20-24 real LLM calls to you, so each batch
will take a few minutes — that's expected, don't interrupt it).

Batch A (injected-error test cases — run this first):
  python eval/run_verification_audit.py --source test_cases --start 0 --end 12 --model opencode/big-pickle --out eval/results/verification_batch_tests.json

CHECKPOINT after Batch A: read the printed catch rate (correct PASS/FAIL
vs. expected, out of 12).
  - If catch rate is >= 80% (10/12 or better): continue to batches B, C, D
    below.
  - If catch rate is < 80%: STOP. Do not run B/C/D. Instead report back
    exactly which test IDs were misclassified and the raw verifier output
    for those (the "reason" fields in the JSON) so it can be diagnosed —
    this likely means the verifier's prompts need tuning, not a batching
    problem, and burning more calls won't help yet.

Batch B (only if Batch A passed):
  python eval/run_verification_audit.py --source eval_set --start 0 --end 10 --model opencode/big-pickle --out eval/results/verification_batch_1.json

Batch C:
  python eval/run_verification_audit.py --source eval_set --start 10 --end 20 --model opencode/big-pickle --out eval/results/verification_batch_2.json

Batch D:
  python eval/run_verification_audit.py --source eval_set --start 20 --end 30 --model opencode/big-pickle --out eval/results/verification_batch_3.json

RULES:
- Do not write, edit, or "improve" any of the Python files. Your job is
  execution only. If something looks wrong in the code, report it, don't
  fix it.
- Do not change the --model flag from opencode/big-pickle unless commands
  are failing because that model isn't what you're authenticated with —
  if so, use whatever model you ARE authenticated with (check with
  `opencode auth list` if unsure) and tell me you changed it and why.
- If any batch command errors out or hangs for more than ~5 minutes, stop,
  capture the full error/output, and report it rather than retrying
  blindly.
- After each batch, confirm the output JSON file exists and paste the
  printed summary lines.

At the end (whether you completed all 4 batches or stopped early at the
checkpoint), give me one final summary: which batches ran, their
summaries, any errors, and the exact file paths written to
eval/results/. I'll hand this off to Claude Code to compute the final
report — you don't need to interpret the results beyond what the script
already prints.
```

---

## 1. Batch plan (reference — already embedded in the prompt above)

| Batch | Source | Range | Items | ~Calls | Output file |
|---|---|---|---|---|---|
| A | `test_cases` | 0–12 | 12 injected-error fixtures | ~24 | `eval/results/verification_batch_tests.json` |
| B | `eval_set` | 0–10 | Q1–Q10 (real eval questions) | ~20 | `eval/results/verification_batch_1.json` |
| C | `eval_set` | 10–20 | Q11–Q20 | ~20 | `eval/results/verification_batch_2.json` |
| D | `eval_set` | 20–30 | Q21–Q30 | ~20 | `eval/results/verification_batch_3.json` |

---

## 3. What "good" looks like

- **Batch A (catch rate):** ≥80% (10/12 correct PASS/FAIL vs. expected).
  If lower, tell me before running B–D — likely a prompt-tuning issue in
  `faithfulness_verifier.py`, not a batching problem.
- **Batches B–D (call failures):** should be 0 or close to it. Any
  `"error"` field in the output JSON means an opencode call failed or
  returned unparseable JSON for that item — worth flagging, not necessarily
  fatal (the summary will show how many).

---

## 4. After all 4 batches are done

Tell me they're done (or just that some failed / looked wrong). I'll read
the 4 output files from `eval/results/`, compute the final catch rate /
pass rate / latency stats, generate `eval/verification_report.md`, and
update `PHASE_2_HANDOVER.md` with the results — no further CLI calls needed
from you at that point.
