"""
Batchable, resumable faithfulness-verification runner.

Two data sources:
  --source test_cases  -> eval/test_verification.py (injected-error fixtures,
                           has an "expected" PASS/FAIL label to score against)
  --source eval_set    -> eval/results/records_hybrid.json (30 real Q&A
                           records already generated in Phase 1, no label --
                           this is the production-realism audit)

Designed to run in small batches (--start/--end) so a single invocation
stays short: each item costs 1 opencode CLI call (claim extraction +
entailment combined into one prompt), or 0 calls if the answer is a
detected pure refusal (out-of-scope questions skipped, not verified).

Resumable: results are written to --out after EVERY item, not just at the
end. If the run is interrupted (rate limit, crash, Ctrl-C), re-running the
exact same command skips items already present in --out and only processes
what's left -- no lost work, no re-spending calls on items already done.

Usage:
    python eval/run_verification_audit.py --source test_cases \\
        --start 0 --end 12 --out eval/results/verification_batch_tests.json

    python eval/run_verification_audit.py --source eval_set \\
        --start 0 --end 10 --out eval/results/verification_batch_1.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.verification.faithfulness_verifier import verify_answer


def load_test_cases(start: int, end: int):
    from eval.test_verification import TEST_CASES
    return TEST_CASES[start:end]


def load_eval_set(start: int, end: int):
    path = PROJECT_ROOT / "eval" / "results" / "records_hybrid.json"
    records = json.loads(path.read_text())
    return records[start:end]


def load_existing(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text())
    except json.JSONDecodeError:
        return []


def save(out_path: Path, results: list[dict]):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))


def run_batch(source: str, start: int, end: int, out_path: Path):
    existing = load_existing(out_path)
    # Only a genuinely successful (non-errored) prior result counts as
    # "done" -- an errored item gets retried, not permanently skipped.
    by_id = {r["id"]: r for r in existing if not r.get("error")}
    n_skip = len(by_id)
    if n_skip:
        print(f"Resuming: {n_skip} items already verified in {out_path}, skipping those.")

    def commit(item_id: str, record: dict):
        by_id[item_id] = record
        save(out_path, list(by_id.values()))  # persist after every item

    if source == "test_cases":
        items = load_test_cases(start, end)
        for item in items:
            if item["id"] in by_id:
                continue
            v = verify_answer(item["answer"], [item["context"]], entity=item.get("entity"))
            correct = (not v.get("error")) and (
                (v["pass"] and item["expected"] == "PASS")
                or (not v["pass"] and item["expected"] == "FAIL")
            )
            commit(item["id"], {
                "id": item["id"],
                "question": item["question"],
                "expected": item["expected"],
                "actual": "PASS" if v["pass"] else "FAIL",
                "correct": correct,
                **v,
            })
            time.sleep(1.5)  # space out calls -- reduces transient CLI failures
    elif source == "eval_set":
        items = load_eval_set(start, end)
        for item in items:
            if item["id"] in by_id:
                continue
            # ticker = first segment of "AAPL_2025-10-31_..." -- but ONLY use
            # the entity hint when every retrieved chunk belongs to the SAME
            # company. Comparison/multi-company answers (chunks from several
            # tickers) must NOT be told "this context is excerpted from X's
            # filing" -- it actively misleads the judge (see the mixed-context
            # q13 case: a single-company entity hint caused 5/6 supported JNJ
            # claims to be marked not_entailed).
            cids = item.get("chunk_ids") or []
            tickers = {c.split("_")[0] for c in cids}
            entity = list(tickers)[0] if len(tickers) == 1 else None
            v = verify_answer(item["answer"], item["contexts"], entity=entity)
            commit(item["id"], {
                "id": item["id"],
                "question": item["question"],
                **v,
            })
            time.sleep(1.5)  # space out calls -- reduces transient CLI failures
    else:
        raise ValueError(f"unknown source: {source}")
    return list(by_id.values())


def summarize(source: str, results: list[dict]):
    n = len(results)
    if n == 0:
        print("No items in this batch range.")
        return
    errors = sum(1 for r in results if r.get("error"))
    print(f"\n=== Batch summary ({source}, {n} items) ===")
    print(f"opencode CLI call failures: {errors}/{n}")
    if source == "test_cases":
        correct = sum(1 for r in results if r["correct"])
        print(f"Catch rate (correct PASS/FAIL vs expected): {correct}/{n} ({correct/n:.0%})")
        for r in results:
            mark = "OK" if r["correct"] else "MISMATCH"
            print(f"  [{mark}] {r['id']}: expected={r['expected']} actual={r['actual']}")
    else:
        skipped = sum(1 for r in results if r.get("skipped"))
        scored = [r for r in results if r.get("score") is not None]
        passed = sum(1 for r in results if r["pass"])
        avg_latency = sum(r["latency_ms"] for r in results) / n
        print(f"Pass rate: {passed}/{n} ({passed/n:.0%})")
        print(f"Skipped (detected refusal, not verified): {skipped}/{n}")
        if scored:
            avg_score = sum(r["score"] for r in scored) / len(scored)
            print(f"Avg faithfulness score (non-skipped, n={len(scored)}): {avg_score:.3f}")
        print(f"Avg latency: {avg_latency:.0f}ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["test_cases", "eval_set"], required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_path = PROJECT_ROOT / args.out
    results = run_batch(args.source, args.start, args.end, out_path)

    print(f"Wrote {len(results)} results to {out_path}")
    summarize(args.source, results)
