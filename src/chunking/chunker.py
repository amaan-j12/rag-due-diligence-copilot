"""
Section-aware chunker for SEC filings.

Strategy:
1. Split the cleaned text into (section_name, section_text) blocks using
   whole-line "Item N[A-Z]. Title" header matches. This deliberately ignores
   the table-of-contents occurrences of the same pattern (those are followed
   by a page number / dotted leader or embedded inline mid-paragraph, not
   standing alone as the start of a body section) by requiring:
     - the header sits on its own line
     - it is not inside the first N% of the document (crude TOC-skip) OR
     - a minimum body-length heuristic: keep only the LAST occurrence of a
       given header text (the real section body always comes after the TOC).
2. Within each section, sub-chunk to 500-800 tokens with 100 token overlap,
   never letting a chunk span two different Item sections.
"""
import re
import json
from pathlib import Path
import tiktoken

ENCODING = tiktoken.get_encoding("cl100k_base")

# Canonical section header line pattern: whole line, starts with "Item",
# a number, optional letter suffix, optional period, then a short title.
HEADER_LINE_RE = re.compile(
    r"^Item\s+(\d{1,2}[A-Z]?)\.?\s+([A-Z][A-Za-z0-9 ,'&\-/]{2,150}?)\.?$"
)


def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def split_into_sections(text: str) -> list[tuple[str, str]]:
    """
    Return list of (section_label, section_text) in document order, using
    only the LAST occurrence of each header text as the real section start
    (earlier occurrences are almost always table-of-contents / cross-refs).
    """
    lines = text.split("\n")
    header_positions = []  # (line_idx, header_label)
    for i, ln in enumerate(lines):
        ln_stripped = ln.strip()
        m = HEADER_LINE_RE.match(ln_stripped)
        if m:
            label = f"Item {m.group(1)} {m.group(2)}".strip()
            header_positions.append((i, label))

    if not header_positions:
        return [("Full Document", text)]

    # Keep only the last occurrence of each distinct normalized header text,
    # AND require it to be past the first occurrence cluster (skip TOC).
    # Heuristic: a TOC block is a dense run of header lines with very few
    # non-header lines between them. Real section starts are spaced apart
    # by hundreds of lines of body text. So: for each header label, keep the
    # occurrence with the LARGEST line_idx (documents don't repeat sections).
    last_occurrence = {}
    for idx, label in header_positions:
        norm = re.sub(r"^Item \d{1,2}[A-Z]?\s+", "", label).strip().lower()
        num = re.match(r"Item (\d{1,2}[A-Z]?)", label).group(1)
        key = num  # dedupe by item number only
        last_occurrence[key] = (idx, label)

    ordered = sorted(last_occurrence.values(), key=lambda x: x[0])

    sections = []
    for j, (idx, label) in enumerate(ordered):
        start = idx + 1  # skip the header line itself
        end = ordered[j + 1][0] if j + 1 < len(ordered) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            sections.append((label, body))

    # Prepend any text before the first real section (cover page / signatures
    # intro) as its own pseudo-section so nothing is silently dropped.
    first_idx = ordered[0][0]
    preamble = "\n".join(lines[:first_idx]).strip()
    if len(preamble) > 200:  # ignore trivial preambles
        sections.insert(0, ("Preamble / Cover Page", preamble))

    return sections


def chunk_section_text(
    section_text: str,
    min_tokens: int = 500,
    max_tokens: int = 800,
    overlap_tokens: int = 100,
) -> list[str]:
    """Token-based sliding window chunking within one section's text."""
    tokens = ENCODING.encode(section_text)
    if len(tokens) <= max_tokens:
        return [section_text] if section_text.strip() else []

    chunks = []
    start = 0
    n = len(tokens)
    step = max_tokens - overlap_tokens
    while start < n:
        end = min(start + max_tokens, n)
        chunk_tokens = tokens[start:end]
        chunk_text = ENCODING.decode(chunk_tokens)
        chunks.append(chunk_text)
        if end == n:
            break
        start += step
    return chunks


def chunk_filing_text(text: str, config: dict) -> list[dict]:
    """
    Returns list of {"section": str, "text": str} chunks for one filing,
    respecting section boundaries.
    """
    ch_cfg = config["chunking"]
    sections = split_into_sections(text)
    results = []
    for section_label, section_text in sections:
        sub_chunks = chunk_section_text(
            section_text,
            min_tokens=ch_cfg["target_tokens_min"],
            max_tokens=ch_cfg["target_tokens_max"],
            overlap_tokens=ch_cfg["overlap_tokens"],
        )
        for sc in sub_chunks:
            results.append({"section": section_label, "text": sc})
    return results


if __name__ == "__main__":
    import sys
    import yaml
    from src.ingestion.clean import clean_filing_file

    config = yaml.safe_load(open("config/retrieval_config.yaml"))
    text = clean_filing_file(sys.argv[1])
    sections = split_into_sections(text)
    print(f"Found {len(sections)} sections:")
    for label, body in sections:
        print(f"  {label:50s} chars={len(body):7d} tokens~={count_tokens(body):6d}")
    chunks = chunk_filing_text(text, config)
    print(f"\nTotal chunks: {len(chunks)}")
    tok_counts = [count_tokens(c["text"]) for c in chunks]
    print(f"token count min/max/avg: {min(tok_counts)}/{max(tok_counts)}/{sum(tok_counts)//len(tok_counts)}")
