"""
Run the full pipeline (retrieval mode X -> generation) over the 30 eval
questions, then score with RAGAS (faithfulness, answer_relevancy,
context_precision, context_recall).

RAGAS's default metrics need an LLM judge + embeddings. Per the locked
decision (Claude CLI first, API fallback, budget-aware), we wrap Claude
CLI/API as a RAGAS-compatible LLM via a thin LangChain shim, and reuse the
local bge embedding model for the RAGAS embeddings (no extra API cost).

Usage:
  python -m eval.run_ragas --mode naive
  python -m eval.run_ragas --mode hybrid
  python -m eval.run_ragas --mode rerank
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.retrieval.retriever import Retriever
from src.generation.generate import generate_answer, HAIKU_MODEL, SONNET_MODEL, _try_anthropic_api, _try_opencode_cli, _try_claude_cli


def load_questions():
    path = PROJECT_ROOT / "eval" / "questions.jsonl"
    qs = []
    with open(path) as f:
        for line in f:
            qs.append(json.loads(line))
    return qs


def run_pipeline(mode: str, judge_model: str = HAIKU_MODEL):
    """Run retrieval + generation for all 30 questions, return list of dicts
    with question, answer, contexts (list[str]), chunk metadata."""
    retriever = Retriever()
    questions = load_questions()
    records = []
    for i, q in enumerate(questions):
        print(f"[{mode}] {i+1}/{len(questions)} {q['id']}: {q['question'][:70]}...")
        chunks = retriever.retrieve(q["question"], mode=mode)
        gen = generate_answer(q["question"], chunks)
        records.append({
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "answer": gen["answer"],
            "contexts": [c["text"] for c in chunks],
            "chunk_ids": [c["chunk_id"] for c in chunks],
            "source": gen["source"],
        })
    return records


def score_with_ragas(records: list[dict], mode: str, judge_model: str = HAIKU_MODEL):
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_core.embeddings import Embeddings as LCEmbeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from sentence_transformers import SentenceTransformer
    import yaml

    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "retrieval_config.yaml"))

    # --- RAGAS judge LLM: uses Anthropic API only (CLI unreliable under RAGAS concurrency) ---
    class ClaudeJudgeLLM(BaseChatModel):
        model_to_use: str = HAIKU_MODEL

        @property
        def _llm_type(self):
            return "claude-judge-api"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            prompt_text = "\n".join(m.content for m in messages if hasattr(m, "content"))
            system = "You are a precise evaluation assistant. Follow the instructions exactly and output only valid JSON."

            # 1. Try opencode with the flagship reasoning model (free, primary)
            out = _try_opencode_cli(system, prompt_text, model="opencode/big-pickle", timeout=120)

            # 2. Fallback to Claude CLI (free, secondary)
            if out is None:
                out = _try_claude_cli(system, prompt_text)

            # 3. Last resort: Anthropic API (paid, tertiary) - only if model is explicitly set to API
            if out is None and self.model_to_use in [HAIKU_MODEL, SONNET_MODEL]:
                out = _try_anthropic_api(system, prompt_text, model=self.model_to_use, max_tokens=4096)

            if out is None:
                raise RuntimeError("RAGAS judge LLM: All sources failed (opencode, claude CLI, and API).")
            gen = ChatGeneration(message=AIMessage(content=out))
            return ChatResult(generations=[gen])

    # --- RAGAS embeddings: reuse local bge model, zero extra cost ---
    class BgeEmbeddings(LCEmbeddings):
        def __init__(self):
            self.model = SentenceTransformer(config["embedding"]["model_name"])

        def embed_documents(self, texts):
            return self.model.encode(texts, normalize_embeddings=True).tolist()

        def embed_query(self, text):
            return self.model.encode([text], normalize_embeddings=True)[0].tolist()

    judge_llm = LangchainLLMWrapper(ClaudeJudgeLLM(model_to_use=judge_model))
    judge_embeddings = LangchainEmbeddingsWrapper(BgeEmbeddings())

    ds = Dataset.from_dict({
        "question": [r["question"] for r in records],
        "answer": [r["answer"] for r in records],
        "contexts": [r["contexts"] for r in records],
        "ground_truth": ["" for _ in records],  # no hand-labeled ground truth; context_recall will be weak signal
    })

    result = evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge_llm,
        embeddings=judge_embeddings,
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["naive", "hybrid", "rerank"], required=True)
    parser.add_argument("--skip-ragas", action="store_true", help="only run pipeline, save records, skip RAGAS scoring")
    parser.add_argument("--judge-model", default=HAIKU_MODEL, help="LLM model for RAGAS judge scoring (default: haiku)")
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True, parents=True)

    # Check if we should reuse existing generation records
    records_path = out_dir / f"records_{args.mode}.json"
    if records_path.exists():
        with open(records_path) as f:
            records = json.load(f)
        print(f"Reusing existing {args.mode} generation records from {records_path}")
    else:
        t0 = time.time()
        records = run_pipeline(args.mode, judge_model=args.judge_model)
        with open(records_path, "w") as f:
            json.dump(records, f, indent=2)
        print(f"Pipeline run done in {time.time()-t0:.1f}s, saved records_{args.mode}.json")

    if args.skip_ragas:
        return

    print("Scoring with RAGAS...")
    result = score_with_ragas(records, args.mode, judge_model=args.judge_model)
    df = result.to_pandas()
    df.to_csv(out_dir / f"ragas_{args.mode}.csv", index=False)

    summary = {k: float(v) for k, v in result._repr_dict.items()} if hasattr(result, "_repr_dict") else dict(result)
    with open(out_dir / f"ragas_summary_{args.mode}.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"RAGAS summary [{args.mode}]:", summary)


if __name__ == "__main__":
    main()
