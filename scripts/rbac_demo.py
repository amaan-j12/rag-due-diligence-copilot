"""
RBAC side-by-side demo (BUILD_SPEC.md Part 6).

Asks the SAME question as two roles and prints both retrieval scopes and the
generated answer for each -- the portfolio-narrative artifact showing that a
role-restricted query never even retrieves (let alone answers from) chunks the
role isn't allowed to see.

Usage:
    python scripts/rbac_demo.py [--mode naive|hybrid|rerank] [--question "..."]
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.retrieval.retriever import Retriever
from src.generation.generate import generate_answer

DEFAULT_QUESTION = "What is JPMorgan Chase's total litigation exposure and what are the main legal risks?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="rerank", choices=["naive", "hybrid", "rerank"])
    ap.add_argument("--question", default=DEFAULT_QUESTION)
    ap.add_argument("--roles", default="analyst,external_contractor")
    ap.add_argument("--no-generate", action="store_true", help="skip LLM generation, retrieval only")
    args = ap.parse_args()

    r = Retriever()
    roles = [x.strip() for x in args.roles.split(",") if x.strip()]

    summary = {"question": args.question, "mode": args.mode, "roles": {}}

    for role in roles:
        print(f"\n{'='*72}\nROLE: {role}\n{'='*72}")
        chunks = r.retrieve(args.question, mode=args.mode, role=role)
        sectors = {c["sector"] for c in chunks}
        eligible = r._count_eligible(role)
        print(f"Retrieved {len(chunks)} chunks | eligible={eligible} | sectors seen: {sectors}")
        for c in chunks:
            print(f"  [{c['chunk_id']}] {c['ticker']:5s} {c['section'][:32]:34s} roles={c['allowed_roles']}")

        summary["roles"][role] = {
            "sectors_seen": sorted(sectors),
            "retrieved": [{"chunk_id": c["chunk_id"], "ticker": c["ticker"], "sector": c["sector"]} for c in chunks],
        }

        if not args.no_generate:
            try:
                result = generate_answer(args.question, chunks)
                print(f"\n--- ANSWER (role={role}, source={result['source']}) ---\n{result['answer']}")
                summary["roles"][role]["answer"] = result["answer"]
            except RuntimeError as e:
                print(f"\nGeneration failed: {e}")

    out_path = PROJECT_ROOT / "eval" / "results" / "rbac_demo.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSaved demo output to {out_path}")


if __name__ == "__main__":
    main()