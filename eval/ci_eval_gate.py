"""
CI eval-gate runner (BUILD_SPEC.md Part 7).

Cheap, deterministic-fail gate: verify a curated 8-question subset of the
eval set (2 single-hop, 4 multi-hop, 2 out-of-scope) and compare the
resulting metrics against config/ci_thresholds.yaml. Exits non-zero if any
metric breaches its bar, so a GitHub Actions workflow can fail the PR.

Two modes:
  --refresh       Run the real verifier (1 opencode CLI call per verified
                  answer; 0 for out-of-scope refusals) over the subset and
                  overwrite eval/results/ci_snapshot.json. Requires a local
                  opencode CLI with the configured model. Use this locally
                  whenever prompt/retrieval/verification config changes, so
                  the committed snapshot reflects current behavior.
  (default)       Load the committed eval/results/ci_snapshot.json and check
                  it against thresholds. No LLM calls -- this is what runs in
                  CI, since GitHub Actions has no authenticated CLI.

Change flow: edit config -> run `python eval/ci_eval_gate.py --refresh` ->
commit the refreshed snapshot with the config change. CI then fails PRs
whose committed snapshot drops below the bars (e.g. a prompt tweak that
made the verifier pass hallucinated answers).

Usage:
    python eval/ci_eval_gate.py --refresh
    python eval/ci_eval_gate.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SNAPSHOT_PATH = PROJECT_ROOT / "eval" / "results" / "ci_snapshot.json"
THRESHOLDS_PATH = PROJECT_ROOT / "config" / "ci_thresholds.yaml"
RECORDS_PATH = PROJECT_ROOT / "eval" / "results" / "records_hybrid.json"
SUBSET_PATH = PROJECT_ROOT / "eval" / "ci_subset.json"


def load_thresholds() -> dict:
    import yaml
    doc = yaml.safe_load(THRESHOLDS_PATH.read_text())
    return doc["thresholds"]


def load_records() -> dict:
    by_id = {}
    for r in json.loads(RECORDS_PATH.read_text()):
        by_id[str(r["id"])] = r
    return by_id


def load_subset() -> list[str]:
    ids = json.loads(SUBSET_PATH.read_text())
    if not isinstance(ids, list) or not ids:
        raise SystemExit("eval/ci_subset.json must be a non-empty list of record ids")
    return [str(i) for i in ids]


def refresh_snapshot() -> list[dict]:
    from src.verification.faithfulness_verifier import verify_answer

    records = load_records()
    subset = load_subset()
    missing = [i for i in subset if i not in records]
    if missing:
        raise SystemExit(f"subset ids not in {RECORDS_PATH.name}: {missing}")

    results = []
    for qid in subset:
        rec = records[qid]
        cids = rec.get("chunk_ids") or []
        tickers = {c.split("_")[0] for c in cids}
        entity = list(tickers)[0] if len(tickers) == 1 else None
        v = verify_answer(rec["answer"], rec["contexts"], entity=entity)
        results.append({
            "id": qid,
            "category": rec.get("category", "unknown"),
            "question": rec["question"],
            **v,
        })
        time.sleep(1.5)
    return results


def metrics_for(results: list[dict]) -> dict:
    n = len(results)
    errors = sum(1 for r in results if r.get("error"))
    skipped = sum(1 for r in results if r.get("skipped"))
    scored = [r for r in results if not r.get("error") and r.get("score") is not None]
    # Only actually-verified (non-skipped) answers count toward the pass rate.
    # Skipped refusals carry pass=True by design but have no scored claims.
    passed = sum(1 for r in scored if r["pass"])

    out_of_scope = [r for r in results if r["category"] == "out_of_scope"]
    oos_dismissed = sum(1 for r in out_of_scope if r.get("skipped")) if out_of_scope else 0

    return {
        "n": n,
        "errors": errors,
        "call_failure_rate": errors / n,
        "skipped": skipped,
        "n_scored": len(scored),
        "answer_pass_rate": (passed / len(scored)) if scored else 0.0,
        "avg_faithfulness": (sum(r["score"] for r in scored) / len(scored)) if scored else None,
        "avg_latency_ms": (sum(r["latency_ms"] for r in results) / n) if n else 0.0,
        "n_out_of_scope": len(out_of_scope),
        "out_of_scope_dismissed_rate": oos_dismissed / len(out_of_scope) if out_of_scope else 1.0,
    }


def compare(metrics: dict, thresholds: dict) -> tuple[bool, list[str]]:
    failures = []

    def check(name, actual, op):
        good = op(actual, thresholds[name])
        if not good:
            failures.append(
                f"{name}: {actual:.3f} vs threshold {thresholds[name]} "
                f"({'OK' if good else 'BREACH'})"
            )
        return good

    check("min_answer_pass_rate", metrics["answer_pass_rate"], lambda a, t: a >= t)
    check("max_call_failure_rate", metrics["call_failure_rate"], lambda a, t: a <= t)
    check("min_out_of_scope_dismissed", metrics["out_of_scope_dismissed_rate"], lambda a, t: a >= t)
    check("max_avg_latency_ms", metrics["avg_latency_ms"], lambda a, t: a <= t)

    avg = metrics["avg_faithfulness"]
    if avg is None:
        failures.append("avg_faithfulness: no scored answers -> cannot gate")
    else:
        if not (thresholds["min_avg_faithfulness"] <= avg <= thresholds["max_avg_faithfulness"]):
            failures.append(
                f"avg_faithfulness: {avg:.3f} outside "
                f"[{thresholds['min_avg_faithfulness']}, {thresholds['max_avg_faithfulness']}]"
            )
    return not failures, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="re-verify subset with real opencode CLI and rewrite snapshot")
    args = ap.parse_args()

    if args.refresh:
        print("Refreshing CI snapshot (real verification via opencode CLI)...")
        results = refresh_snapshot()
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(results, indent=2))
        print(f"Wrote {len(results)} results to {SNAPSHOT_PATH}")
    else:
        if not SNAPSHOT_PATH.exists():
            raise SystemExit(
                f"{SNAPSHOT_PATH} missing. Run `python eval/ci_eval_gate.py --refresh` "
                "locally (needs opencode CLI) to generate it."
            )
        results = json.loads(SNAPSHOT_PATH.read_text())

    thresholds = load_thresholds()
    metrics = metrics_for(results)
    ok, failures = compare(metrics, thresholds)

    print(f"\n=== CI eval gate ({'refreshed' if args.refresh else 'snapshot'} mode) ===")
    print(f"questions: {metrics['n']}  "
          f"(single: {sum(1 for r in results if r['category']=='single_hop')}, "
          f"multi: {sum(1 for r in results if r['category']=='multi_hop')}, "
          f"oos: {metrics['n_out_of_scope']})")
    print(f"scored: {metrics['n_scored']}, skipped (refusal fast-path): {metrics['skipped']}")
    for r in results:
        state = "ERROR" if r.get("error") else ("skip" if r.get("skipped") else
                                                ("PASS" if r["pass"] else "FAIL"))
        print(f"  [{state:>5}] {r['id']} ({r['category']}): "
              f"score={r.get('score')} claim_ver={r['total_claims']}/{r['refused_claims']}/{r['pipeline_claims']}")
    print(f"\nanswer_pass_rate:  {metrics['answer_pass_rate']:.3f}  (bar >= "
          f"{thresholds['min_answer_pass_rate']})")
    print(f"avg_faithfulness:  {str(round(metrics['avg_faithfulness'], 3) if metrics['avg_faithfulness'] is not None else 'None'):>7}  "
          f"(bar [{thresholds['min_avg_faithfulness']}, {thresholds['max_avg_faithfulness']}])")
    print(f"oos dismissal:     {metrics['out_of_scope_dismissed_rate']:.3f}  "
          f"(bar >= {thresholds['min_out_of_scope_dismissed']})")
    print(f"call failures:     {metrics['call_failure_rate']:.3f}  "
          f"(bar <= {thresholds['max_call_failure_rate']})")
    print(f"avg latency:       {metrics['avg_latency_ms']:.0f}ms  "
          f"(bar <= {thresholds['max_avg_latency_ms']}ms)")

    if failures:
        print(f"\nGATE FAILED ({len(failures)} breach(es)):")
        for f in failures:
            print(f"  - {f}")
        print("\nPrompt/retrieval change likely regressed faithfulness. "
              "See eval/verification_report.md and --refresh locally.")
        return 1
    print("\nGATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())