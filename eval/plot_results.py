"""
Build the before/after bar chart comparing naive vector-only -> hybrid ->
hybrid+rerank across the four RAGAS metrics. Saved as PNG.
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"

MODES = ["naive", "hybrid", "rerank"]
MODE_LABELS = {"naive": "Naive\n(vector-only)", "hybrid": "+ Hybrid\n(BM25+dense RRF)", "rerank": "+ Rerank\n(cross-encoder)"}
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def load_summary(mode):
    path = RESULTS_DIR / f"ragas_summary_{mode}.json"
    if not path.exists():
        return None
    return json.load(open(path))


def main():
    summaries = {mode: load_summary(mode) for mode in MODES}
    missing = [m for m, s in summaries.items() if s is None]
    if missing:
        print(f"WARNING: missing RAGAS summaries for modes: {missing}. Run eval/run_ragas.py for those first.")

    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(METRICS))
    width = 0.25
    colors = ["#9ca3af", "#60a5fa", "#16a34a"]

    for i, mode in enumerate(MODES):
        s = summaries[mode]
        vals = [s.get(m, 0) if s else 0 for m in METRICS]
        ax.bar(x + (i - 1) * width, vals, width, label=MODE_LABELS[mode], color=colors[i])

    ax.set_xticks(x)
    ax.set_xticklabels([m.replace("_", " ").title() for m in METRICS])
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("RAGAS score")
    ax.set_title("RAG Due Diligence Copilot -- Retrieval Pipeline Before/After (RAGAS, n=30 questions)")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()

    out_path = RESULTS_DIR / "before_after_ragas.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved chart -> {out_path}")


if __name__ == "__main__":
    main()
