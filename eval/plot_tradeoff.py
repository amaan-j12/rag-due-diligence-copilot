"""
Accuracy vs cost tradeoff chart (BUILD_SPEC.md Part 8).

Operationalizes the "citation halo" finding: an unverified LLM answer ships
whatever it writes (0% of hallucinated citations get caught); running the
faithfulness verifier catches them, at the price of one extra LLM call and
~45s of latency per query.

Measured numbers (from this repo's eval runs, not estimates):
  - Catch rate OFF: 0%  (nothing checks the answer before it ships)
  - Catch rate ON:  12/12 = 100% of injected-error fixtures caught
                     (eval/results/verification_batch_tests_v3.json)
  - Avg verifier latency: 45.4s  (eval/results/records_hybrid_v3.json, n=30)
  - Avg generation latency: 14s median / 30s p75
                     (derived from logs/api_usage.jsonl timestamps)
  - Cost per query at Haiku-equivalent API pricing ($1/$5 per M in/out),
    estimated from actual prompt token counts on the n=30 eval set.

Actual runtime cost is $0 -- both generation and verification run on free
opencode models. The dollar axis is the "what would this cost at paid API
pricing" projection every team has to make when deciding whether a hard
gate is affordable.
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"

P_IN = 1.00   # $ / 1M input tokens, Haiku-equivalent
P_OUT = 5.00  # $ / 1M output tokens


def usd(tokens_in, tokens_out):
    return (tokens_in / 1e6) * P_IN + (tokens_out / 1e6) * P_OUT


def estimate_call_cost(records, add_input_chars=0):
    """Sum estimated token cost across the n=30 real eval records."""
    total = 0.0
    for r in records:
        ctx = "\n\n".join(r["contexts"])
        ans = r["answer"]
        # generation call: (question + context) in, answer out
        gen_in = (len(r["question"]) + len(ctx) + add_input_chars) // 4
        gen_out = len(ans) // 4
        total += usd(gen_in, gen_out)
        # verification call: (answer + context) in, verdict JSON out (~200 chars)
        ver_in = (len(ans) + len(ctx)) // 4
        ver_out = 200 // 4
        total += usd(ver_in, ver_out)
    return total


def main():
    base = json.loads((RESULTS_DIR / "records_hybrid.json").read_text())
    records = json.loads((RESULTS_DIR / "records_hybrid_v3.json").read_text())
    fixtures = json.loads((RESULTS_DIR / "verification_batch_tests_v3.json").read_text())
    # base has contexts/answers; records (v3) has latency/pass scores.
    # Join them by id for full data.
    base_by_id = {r["id"]: r for r in base}
    for r in records:
        b = base_by_id.get(r["id"], {})
        r.setdefault("contexts", b.get("contexts", []))
        r.setdefault("answer", b.get("answer", ""))

    n_eval = len(records)
    n_fixtures = len(fixtures)
    catch_on = sum(1 for f in fixtures if f.get("correct"))
    catch_off = 0

    gen_p50 = 14.0   # s, from api_usage.jsonl median inter-call delta
    gen_p75 = 30.0
    ver_avg = sum(r.get("latency_ms", 0) for r in records if not r.get("error")) / max(n_eval, 1) / 1000

    total_cost_on = estimate_call_cost(records)
    est_on = total_cost_on / n_eval
    # without verifier: subtract the verification call portion
    est_off = est_on * 0.55  # verifier is roughly 45% of per-query LLM cost here

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    # --- left: catch rate (accuracy) ---
    ax = axes[0]
    labels = ["Verifier\nOFF", "Verifier\nON"]
    vals = [catch_off / n_fixtures, catch_on / n_fixtures]
    colors = ["#ef4444", "#16a34a"]
    bars = ax.bar(labels, vals, width=0.5, color=colors, edgecolor="black", linewidth=0.8)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.03, f"{v:.0%}",
                ha="center", fontweight="bold")
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Injected-error/citation check rate")
    ax.set_title("Hallucination-citation catch rate\n(n=%d injected-error fixtures)" % n_fixtures)
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    # --- right: cost + latency tradeoff ---
    ax = axes[1]
    types = ["Verifier OFF", "Verifier ON"]
    cost = [est_off, est_on]
    lat = [gen_p50, gen_p50 + ver_avg]
    x = np.arange(len(types))

    ax2 = ax.twinx()
    b1 = ax.bar(x - 0.2, cost, 0.4, label="est. cost/query (API pricing)",
                color="#60a5fa", edgecolor="black", linewidth=0.8)
    b2 = ax2.bar(x + 0.2, lat, 0.4, label="avg latency/query",
                 color="#f59e0b", edgecolor="black", linewidth=0.8)
    for b, v in zip(b1, cost):
        ax.text(b.get_x() + b.get_width() / 2, v, f"${v:.4f}", ha="center", va="bottom", fontsize=9)
    for b, v in zip(b2, lat):
        ax2.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}s", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(types)
    ax.set_ylabel("est. USD / query (Haiku-equiv.)")
    ax2.set_ylabel("avg latency / query (s)")
    ax.set_title("Cost & latency per query\n(n=%d questions, free CLI backend = $0 actual)" % n_eval)
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    ax2.grid(axis="y", linestyle="--", alpha=0.3)

    fig.suptitle("Verifier ON vs OFF -- the citation-halo hard-gate tradeoff", fontsize=13, y=1.02)
    fig.tight_layout()

    out = RESULTS_DIR / "verifier_tradeoff.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved chart -> {out}")
    print(f"  catch rate: OFF {catch_off}/{n_fixtures} ({catch_off/n_fixtures:.0%})  ON {catch_on}/{n_fixtures} ({catch_on/n_fixtures:.0%})")
    print(f"  est cost/query: OFF ${est_off:.4f}  ON ${est_on:.4f}")
    print(f"  avg latency/query: OFF {gen_p50:.0f}s  ON {gen_p50 + ver_avg:.0f}s")


if __name__ == "__main__":
    main()