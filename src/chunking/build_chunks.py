"""
Master ingestion + chunking driver.

Reads data/manifest.json, cleans each filing's HTML, section-splits +
token-chunks it, and writes every chunk (with full metadata) as one line
of JSON to data/chunks.jsonl.

Chunk metadata schema (BUILD_SPEC.md Part 4 + Part 6 RBAC placeholder):
  chunk_id, ticker, sector, company, form, filing_date, section,
  source_url, local_path, chunk_index_in_section, text, token_count,
  allowed_roles
"""
import json
import hashlib
import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ingestion.clean import clean_filing_file
from src.chunking.chunker import chunk_filing_text, count_tokens
from src.rbac.policy import allowed_roles_for


def make_chunk_id(ticker: str, form: str, filing_date: str, section: str, idx: int) -> str:
    raw = f"{ticker}|{form}|{filing_date}|{section}|{idx}"
    h = hashlib.sha1(raw.encode()).hexdigest()[:10]
    return f"{ticker}_{filing_date}_{h}"


def resolve_local_path(manifest_entry: dict) -> str:
    """
    manifest.json's local_path points at the OLD project location
    (Content OS/rag-due-diligence-copilot/...). Resolve against the actual
    current project's data/raw/ directory instead, since that's where the
    files really live now.
    """
    ticker = manifest_entry["ticker"]
    filename = os.path.basename(manifest_entry["local_path"])
    candidate = PROJECT_ROOT / "data" / "raw" / ticker / filename
    if candidate.exists():
        return str(candidate)
    # fall back to whatever the manifest says, in case it's already correct
    return manifest_entry["local_path"]


def main():
    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "retrieval_config.yaml"))
    manifest = json.load(open(PROJECT_ROOT / "data" / "manifest.json"))

    out_path = PROJECT_ROOT / "data" / "chunks.jsonl"
    total_chunks = 0
    total_filings = 0
    failures = []

    with open(out_path, "w") as out_f:
        for entry in manifest:
            local_path = resolve_local_path(entry)
            if not os.path.exists(local_path):
                failures.append((entry.get("ticker"), entry.get("local_path"), "file not found"))
                continue
            try:
                text = clean_filing_file(local_path)
                chunks = chunk_filing_text(text, config)
            except Exception as e:
                failures.append((entry.get("ticker"), local_path, str(e)))
                continue

            section_counters = {}
            for chunk in chunks:
                section = chunk["section"]
                idx = section_counters.get(section, 0)
                section_counters[section] = idx + 1
                chunk_id = make_chunk_id(
                    entry["ticker"], entry["form"], entry["filing_date"], section, idx
                )
                record = {
                    "chunk_id": chunk_id,
                    "ticker": entry["ticker"],
                    "sector": entry["sector"],
                    "company": entry["company"],
                    "form": entry["form"],
                    "filing_date": entry["filing_date"],
                    "section": section,
                    "source_url": entry["source_url"],
                    "local_path": local_path,
                    "chunk_index_in_section": idx,
                    "text": chunk["text"],
                    "token_count": count_tokens(chunk["text"]),
                    "allowed_roles": allowed_roles_for(entry["sector"]),
                }
                out_f.write(json.dumps(record) + "\n")
                total_chunks += 1
            total_filings += 1
            print(f"  [{total_filings}/{len(manifest)}] {entry['ticker']} {entry['form']} {entry['filing_date']}: {len(chunks)} chunks")

    print(f"\nDone. {total_filings}/{len(manifest)} filings processed, {total_chunks} total chunks -> {out_path}")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures:
            print(" ", f)


if __name__ == "__main__":
    main()
