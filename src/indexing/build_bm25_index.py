"""
Build an in-process BM25 keyword index over the same chunk set used for the
dense (Qdrant) index. Saved to a pickle file for fast reload at query time.
"""
import json
import pickle
import re
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TOKEN_RE = re.compile(r"[A-Za-z0-9%$.]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def main():
    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "retrieval_config.yaml"))
    chunks_path = PROJECT_ROOT / "data" / "chunks.jsonl"

    chunks = []
    with open(chunks_path) as f:
        for line in f:
            chunks.append(json.loads(line))
    print(f"Loaded {len(chunks)} chunks")

    corpus_tokens = [tokenize(c["text"]) for c in chunks]
    print("Building BM25 index...")
    bm25 = BM25Okapi(corpus_tokens)

    index_path = PROJECT_ROOT / config["bm25"]["index_path"].lstrip("./")
    index_path.parent.mkdir(parents=True, exist_ok=True)

    # Store the bm25 model + the parallel chunk metadata (minus full text
    # duplication isn't necessary -- keep chunk_id list so we can look up
    # full records from chunks.jsonl at query time, cheaply, by id).
    chunk_ids = [c["chunk_id"] for c in chunks]
    with open(index_path, "wb") as f:
        pickle.dump({"bm25": bm25, "chunk_ids": chunk_ids}, f)

    print(f"Saved BM25 index ({len(chunk_ids)} docs) -> {index_path}")


if __name__ == "__main__":
    main()
