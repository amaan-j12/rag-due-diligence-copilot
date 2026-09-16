"""
Embed all chunks in data/chunks.jsonl with BAAI/bge-base-en-v1.5 and index
them into a LOCAL EMBEDDED Qdrant instance (no server, no cloud, no API key
-- QdrantClient(path=...) per the locked decision).
"""
import json
import sys
from pathlib import Path

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_chunks(path: Path):
    chunks = []
    with open(path) as f:
        for line in f:
            chunks.append(json.loads(line))
    return chunks


def main():
    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "retrieval_config.yaml"))
    emb_cfg = config["embedding"]
    qcfg = config["qdrant"]

    chunks_path = PROJECT_ROOT / "data" / "chunks.jsonl"
    chunks = load_chunks(chunks_path)
    print(f"Loaded {len(chunks)} chunks from {chunks_path}")

    print(f"Loading embedding model {emb_cfg['model_name']} ...")
    model = SentenceTransformer(emb_cfg["model_name"])

    texts = [c["text"] for c in chunks]
    print("Embedding chunks (this may take a few minutes on CPU)...")
    embeddings = model.encode(
        texts,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=emb_cfg["normalize"],
    )
    dim = embeddings.shape[1]
    print(f"Embedded {len(embeddings)} chunks, dim={dim}")
    assert dim == emb_cfg["dimension"], f"config dim {emb_cfg['dimension']} != actual {dim}"

    qdrant_path = str(PROJECT_ROOT / qcfg["path"].lstrip("./"))
    print(f"Opening local embedded Qdrant at {qdrant_path}")
    client = QdrantClient(path=qdrant_path)

    collection_name = qcfg["collection_name"]
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=dim,
            distance=Distance.COSINE if qcfg["distance"] == "Cosine" else Distance.DOT,
        ),
    )

    points = []
    for i, (chunk, vec) in enumerate(zip(chunks, embeddings)):
        payload = {k: v for k, v in chunk.items() if k != "text"}
        payload["text"] = chunk["text"]
        points.append(PointStruct(id=i, vector=vec.tolist(), payload=payload))

    print(f"Upserting {len(points)} points into Qdrant collection '{collection_name}'...")
    batch_size = 256
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection_name, points=points[i : i + batch_size])

    count = client.count(collection_name=collection_name).count
    print(f"Done. Collection '{collection_name}' now has {count} points.")


if __name__ == "__main__":
    main()
