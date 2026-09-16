"""
Retrieval layer: dense (Qdrant), sparse (BM25), RRF fusion, and cross-encoder
reranking. Exposes three retrieval modes used by the eval harness:

  - "naive"  : dense vector-only, top-K straight from Qdrant
  - "hybrid" : dense + BM25 merged via Reciprocal Rank Fusion
  - "rerank" : hybrid fused top-20, cross-encoder reranked down to top-5
"""
import json
import pickle
import re
import time
from pathlib import Path
from functools import lru_cache

import yaml
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer, CrossEncoder

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TOKEN_RE = re.compile(r"[A-Za-z0-9%$.]+")

_audit_log_path = PROJECT_ROOT / "logs" / "retrieval_audit.jsonl"
_audit_log_path.parent.mkdir(parents=True, exist_ok=True)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


class Retriever:
    def __init__(self, config_path: str = None):
        config_path = config_path or (PROJECT_ROOT / "config" / "retrieval_config.yaml")
        self.config = yaml.safe_load(open(config_path))

        emb_cfg = self.config["embedding"]
        qcfg = self.config["qdrant"]

        print("Loading embedding model...")
        self.embed_model = SentenceTransformer(emb_cfg["model_name"])
        self.query_instruction = emb_cfg.get("query_instruction", "")
        self.normalize = emb_cfg["normalize"]

        qdrant_path = str(PROJECT_ROOT / qcfg["path"].lstrip("./"))
        self.qdrant = QdrantClient(path=qdrant_path)
        self.collection_name = qcfg["collection_name"]

        bm25_path = PROJECT_ROOT / self.config["bm25"]["index_path"].lstrip("./")
        with open(bm25_path, "rb") as f:
            bm25_data = pickle.load(f)
        self.bm25 = bm25_data["bm25"]
        self.bm25_chunk_ids = bm25_data["chunk_ids"]

        # Load full chunk records once, indexed by chunk_id, for BM25 lookups
        # and for assembling final context (Qdrant payload also has the text,
        # but we need this for BM25-only hits).
        self.chunks_by_id = {}
        chunks_path = PROJECT_ROOT / "data" / "chunks.jsonl"
        with open(chunks_path) as f:
            for line in f:
                rec = json.loads(line)
                self.chunks_by_id[rec["chunk_id"]] = rec

        self._reranker = None  # lazy-loaded

    @property
    def reranker(self):
        if self._reranker is None:
            print("Loading cross-encoder reranker...")
            self._reranker = CrossEncoder(self.config["reranker"]["model_name"])
        return self._reranker

    def embed_query(self, query: str):
        text = f"{self.query_instruction}{query}"
        vec = self.embed_model.encode([text], normalize_embeddings=self.normalize)[0]
        return vec.tolist()

    def dense_search(self, query: str, top_k: int = None, role: str = None):
        top_k = top_k or self.config["retrieval"]["dense_top_k"]
        vec = self.embed_query(query)
        query_filter = None
        if role:
            from qdrant_client.models import Filter, FieldCondition, MatchAny
            query_filter = Filter(
                must=[FieldCondition(key="allowed_roles", match=MatchAny(any=[role]))]
            )
        results = self.qdrant.query_points(
            collection_name=self.collection_name,
            query=vec,
            limit=top_k,
            query_filter=query_filter,
        ).points
        out = []
        for r in results:
            payload = dict(r.payload)
            payload["score"] = r.score
            out.append(payload)
        return out

    def bm25_search(self, query: str, top_k: int = None, role: str = None):
        top_k = top_k or self.config["retrieval"]["bm25_top_k"]
        tokens = tokenize(query)
        scores = self.bm25.get_scores(tokens)
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked_idx:
            if len(out) >= top_k:
                break
            chunk_id = self.bm25_chunk_ids[i]
            rec = self.chunks_by_id.get(chunk_id)
            if rec is None:
                continue
            if role and role not in rec.get("allowed_roles", []):
                continue
            payload = dict(rec)
            payload["score"] = float(scores[i])
            out.append(payload)
        return out

    def rrf_fuse(self, ranked_lists: list[list[dict]], k: int = None, top_k: int = None):
        """Reciprocal Rank Fusion over multiple ranked lists of chunk dicts."""
        k = k or self.config["retrieval"]["rrf_k"]
        top_k = top_k or self.config["retrieval"]["fused_top_k"]

        rrf_scores = {}
        chunk_lookup = {}
        for ranked_list in ranked_lists:
            for rank, item in enumerate(ranked_list):
                cid = item["chunk_id"]
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
                chunk_lookup[cid] = item

        fused_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        out = []
        for cid in fused_ids[:top_k]:
            item = dict(chunk_lookup[cid])
            item["rrf_score"] = rrf_scores[cid]
            out.append(item)
        return out

    def rerank(self, query: str, candidates: list[dict], top_k: int = None):
        top_k = top_k or self.config["retrieval"]["rerank_top_k"]
        pairs = [(query, c["text"]) for c in candidates]
        scores = self.reranker.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]

    def retrieve(self, query: str, mode: str = "rerank", role: str = None) -> list[dict]:
        """
        mode: "naive" | "hybrid" | "rerank"
        """
        if mode == "naive":
            dense_top_k = self.config["retrieval"]["rerank_top_k"]  # match final size for fair eval
            hits = self.dense_search(query, top_k=max(dense_top_k, 5), role=role)[: max(dense_top_k, 5)]
        else:
            dense_hits = self.dense_search(query, role=role)
            bm25_hits = self.bm25_search(query, role=role)
            fused = self.rrf_fuse([dense_hits, bm25_hits])

            if mode == "hybrid":
                hits = fused[: self.config["retrieval"]["rerank_top_k"]]
            elif mode == "rerank":
                hits = self.rerank(query, fused)
            else:
                raise ValueError(f"unknown retrieval mode: {mode}")

        self._log_audit(query, mode, role, hits)
        return hits

    def _count_eligible(self, role: str | None) -> int:
        """Number of chunks the requesting role is allowed to see (audit trail)."""
        if not role:
            return len(self.chunks_by_id)
        return sum(1 for r in self.chunks_by_id.values() if role in r.get("allowed_roles", []))

    def _log_audit(self, query: str, mode: str, role: str | None, hits: list[dict]):
        """Append one JSONL audit record per retrieval (BUILD_SPEC.md Part 6):
        which role asked, what they saw eligible vs actually retrieved."""
        rec = {
            "ts": time.time(),
            "query": query,
            "role": role,
            "mode": mode,
            "eligible_chunks": self._count_eligible(role),
            "retrieved_count": len(hits),
            "retrieved": [
                {
                    "chunk_id": c["chunk_id"],
                    "ticker": c["ticker"],
                    "sector": c["sector"],
                    "allowed_roles": c.get("allowed_roles", []),
                }
                for c in hits
            ],
        }
        with open(_audit_log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    import sys
    r = Retriever()
    q = sys.argv[1] if len(sys.argv) > 1 else "What are Apple's main risk factors related to litigation?"
    for mode in ["naive", "hybrid", "rerank"]:
        print(f"\n=== mode={mode} ===")
        hits = r.retrieve(q, mode=mode)
        for h in hits:
            print(f"  {h['chunk_id']:30s} {h['ticker']:6s} {h['section']:35s} score={h.get('rerank_score', h.get('rrf_score', h.get('score')))}")
