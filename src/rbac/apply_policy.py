"""
Apply the RBAC policy to the corpus + dense index IN PLACE.

1. Recomputes `allowed_roles` for every chunk in data/chunks.jsonl from
   config/rbac_policy.yaml (per-sector restriction). Metadata only — chunk
   vectors and text are untouched, so NO re-embedding happens.
2. Syncs the `allowed_roles` field into the existing Qdrant payloads by point
   ID. Qdrant point IDs are assigned 0..N-1 in the exact order of
   chunks.jsonl (build_qdrant_index.py), so the mapping is index-aligned.
3. The BM25 index needs NO rebuild: it stores only chunk_ids, and the
   retriever loads full chunk records (incl. allowed_roles) live from
   chunks.jsonl at query time.

Idempotent: safe to run repeatedly.

Usage:
    python src/rbac/apply_policy.py [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.rbac.policy import allowed_roles_for


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = ap.parse_args()

    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "retrieval_config.yaml"))
    chunks_path = PROJECT_ROOT / "data" / "chunks.jsonl"

    chunks = []
    with open(chunks_path) as f:
        for line in f:
            chunks.append(json.loads(line))

    changed = 0
    for rec in chunks:
        new_roles = allowed_roles_for(rec["sector"])
        if set(rec.get("allowed_roles", [])) != set(new_roles):
            changed += 1
        rec["allowed_roles"] = new_roles

    print(f"Loaded {len(chunks)} chunks; {changed} need role updates.")

    from collections import Counter
    dist = Counter(tuple(c["allowed_roles"]) for c in chunks)
    print("Post-policy allowed_roles distribution:")
    for roles, n in dist.most_common():
        print(f"  {n:6d} chunks -> {list(roles)}")

    if args.dry_run:
        print("Dry run — no changes written.")
        return

    with open(chunks_path, "w") as f:
        for rec in chunks:
            f.write(json.dumps(rec) + "\n")
    print(f"Updated data/chunks.jsonl ({len(chunks)} chunks).")

    # Sync Qdrant payloads in place (no re-embedding). Point ID i == chunks[i],
    # because build_qdrant_index.py assigns ids 0..N-1 in chunks.jsonl order.
    qcfg = config["qdrant"]
    qdrant_path = str(PROJECT_ROOT / qcfg["path"].lstrip("./"))
    from qdrant_client import QdrantClient

    client = QdrantClient(path=qdrant_path)
    coll = qcfg["collection_name"]
    if not client.collection_exists(coll):
        print(f"WARNING: collection '{coll}' not found at {qdrant_path}; skipping Qdrant sync.")
        return

    # set_payload merges (does NOT replace) — existing payload fields preserved.
    # Re-embedding is avoided entirely; only metadata is touched.
    # There are only 2 distinct allowed_roles sets in the policy, so batch
    # by role-set to minimize Qdrant API calls.
    from collections import defaultdict
    id_groups: dict[tuple, list[int]] = defaultdict(list)
    for i, rec in enumerate(chunks):
        key = tuple(rec["allowed_roles"])
        id_groups[key].append(i)

    for role_tuple, ids in id_groups.items():
        # set_payload accepts a list of point IDs and applies the same payload.
        PAYLOAD_BATCH = 512
        for start in range(0, len(ids), PAYLOAD_BATCH):
            client.set_payload(
                collection_name=coll,
                payload={"allowed_roles": list(role_tuple)},
                points=ids[start : start + PAYLOAD_BATCH],
            )
    print(f"Synced allowed_roles into {sum(len(v) for v in id_groups.values())} Qdrant payloads (in place, no re-embedding).")


if __name__ == "__main__":
    main()