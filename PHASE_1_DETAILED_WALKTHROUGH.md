# Phase 1 — The Complete Walkthrough (build this yourself, step by step)

This document explains **every single thing built so far**, in the order it was built, with the actual code, why each decision was made, worked examples with real numbers, and diagrams. The goal: after reading this, you should be able to rebuild this entire pipeline from an empty folder without needing anyone to hand you the code.

Companion docs in this folder: `BUILD_SPEC.md` (the original architecture spec this implements) and `RESUME_BUILD.md` (where the build currently stands and how to finish it).

---

## The mental model first

Before any code, understand what you're actually building in one paragraph:

You have 90 messy HTML documents. You want to be able to ask a question in English and get back a trustworthy, cited answer. To do that, you need to (1) turn messy HTML into clean, labeled chunks of text, (2) make those chunks searchable two different ways — by *meaning* and by *exact words* — (3) combine those two search results intelligently, (4) re-rank the combined results with a smarter (but slower) model so only the best ones survive, (5) hand the best chunks to an LLM with strict instructions to only answer from what it's given and cite everything, and (6) measure, with numbers, whether each of those steps actually helped. That's the whole system. Everything below is the mechanics of those six ideas.

```
 [90 raw .htm files]
        │
        ▼
 ① CLEAN (strip HTML → plain text)
        │
        ▼
 ② SECTION-SPLIT + CHUNK (500-800 token pieces, tagged with metadata)
        │
        ├──────────────────────────┐
        ▼                          ▼
 ③ DENSE INDEX (Qdrant)     ④ SPARSE INDEX (BM25)
   "search by meaning"         "search by exact words"
        │                          │
        └───────────┬──────────────┘
                     ▼
        ⑤ RRF FUSION (merge both rankings into one)
                     ▼
        ⑥ CROSS-ENCODER RERANK (top-20 → top-5, precision pass)
                     ▼
        ⑦ PROMPT ASSEMBLY (versioned template + citation rules)
                     ▼
        ⑧ GENERATION (Claude CLI, API fallback)
                     ▼
        ⑨ EVAL (RAGAS scores this whole pipeline, 3 ways, with real numbers)
```

---

## Step 0 — Environment setup

```bash
cd "/Users/mohammadamaan/Claude Projects/RAG Due Diligence Copilot"
python3.11 -m venv venv
source venv/bin/activate
pip install llama-index qdrant-client sentence-transformers rank_bm25 \
  "ragas==0.2.15" "langchain==0.3.30" "langchain-community==0.3.31" \
  "langchain-core==0.3.86" streamlit pyyaml matplotlib beautifulsoup4 \
  anthropic tiktoken lxml datasets
```

**Why Python 3.11 specifically, not whatever's newest:** this was a real failure hit during the build. Python 3.14 (newest at build time) didn't have compatible pre-built wheels for several ML packages (torch, sentence-transformers) — installs either failed outright or pulled broken versions. Pin to 3.11 (well-supported by the ML ecosystem) and this problem disappears. **Lesson: for ML/data-science Python work, don't assume "newest Python" is safe — check package compatibility first.**

**Why `ragas==0.2.15` specifically, not latest:** latest RAGAS (0.4.x at build time) pulled in a newer `langchain-community` that had a broken import path (`ChatVertexAI` moved/renamed). Pinning RAGAS to 0.2.15 and pinning the langchain family alongside it avoided the conflict. **Lesson: when a fast-moving library breaks, don't fight the latest version — pin to a known-compatible set and move on.**

---

## Step 1 — Download the corpus (already covered in depth earlier — recap only)

Fetch SEC EDGAR's public `company_tickers.json` to map tickers → CIK numbers, then hit `data.sec.gov/submissions/CIK{cik}.json` per company to get every filing they've made, filter to `10-K`/`10-Q`, and download the actual filing HTML from `www.sec.gov/Archives/edgar/data/...`. No API key. Must send a real `User-Agent` header (SEC requires this) and respect their ~10 req/sec limit — the build used `time.sleep(0.15)` between calls to stay safely under it.

**Result:** `data/raw/{TICKER}/*.htm` (90 files, 30 companies, 387MB) and `data/manifest.json` (one row per filing: ticker, sector, company, form, filing_date, source_url, local_path).

**Why this matters as step 1, not an afterthought:** everything downstream depends on the manifest being accurate — chunk metadata, citations, and even the RBAC placeholder all trace back to fields set here. Get the manifest schema right first.

---

## Step 2 — Clean the HTML (`src/ingestion/clean.py`)

### The problem this solves

SEC filings are inline-XBRL HTML — they have `<script>` tags, `<style>` tags, hidden XBRL metadata elements (`<ix:header>`, `<ix:hidden>`), huge `<table>` structures for financial statements, and repeated legal boilerplate on every page. If you feed this raw into a chunker, you get garbage: HTML tags mixed into your "text," financial tables turned into unreadable character soup, and the same disclaimer paragraph repeated 40 times bloating your token count.

### The actual code, explained

```python
BLOCK_TAGS = ["p", "div", "tr", "br", "h1", "h2", "h3", "h4", "li", "table"]

def html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "lxml")

    # Drop non-visible / non-content elements entirely.
    for tag in soup(["script", "style", "head", "ix:header", "ix:hidden"]):
        tag.decompose()

    # Insert newlines at block boundaries so paragraph/row structure survives
    # the text extraction (otherwise BeautifulSoup collapses everything).
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")

    text = soup.get_text()
```

**Why insert newlines at block boundaries before calling `.get_text()`:** if you skip this, BeautifulSoup's `.get_text()` just concatenates all text nodes with no separators — a paragraph followed by a table row followed by another paragraph all run together into one unreadable line. Appending `"\n"` to every block-level tag *before* extraction means each visual "block" in the original document becomes its own line in the output — which is exactly what the next step (section-header detection) needs, because it looks for header text that's *alone on its own line*.

Then whitespace/character normalization:

```python
text = text.replace("\xa0", " ").replace("’", "'")...
text = re.sub(r"[ \t]+", " ", text)                    # collapse runs of spaces
text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)     # cap blank lines at 1
text = re.sub(r" *\n *", "\n", text)                    # trim space around newlines
```

**Why normalize curly quotes/dashes to plain ASCII:** SEC filings mix `’` (curly apostrophe), `–`/`—` (en/em dash) inconsistently. If you don't normalize these, your tokenizer counts them as different characters than a plain `'` or `-`, and search/matching gets subtly worse for no good reason. Cheap fix, do it early.

Then line-level junk filtering:

```python
for ln in lines:
    if re.fullmatch(r"[\.\-–—_ ]{3,}", ln):   # dot-leaders like "......."
        continue
    if re.fullmatch(r"\d{1,4}", ln):           # bare page numbers
        continue
    cleaned_lines.append(ln)
```

**Why this specific pattern:** table-of-contents lines in SEC filings look like `Item 1A. Risk Factors ..................... 14` — after block-splitting, the dot-leader and page number sometimes end up on their own lines. This regex strips those specific junk patterns without being a general-purpose "remove short lines" filter (which would also wrongly strip real short sentences).

Then duplicate-line collapsing:

```python
dedup_lines = []
prev = None
for ln in text.split("\n"):
    if ln == prev:
        continue
    dedup_lines.append(ln)
    prev = ln
```

**Why:** legal boilerplate and table headers often repeat identically on consecutive lines after HTML extraction (e.g., a table header row rendered once per visual row in some layouts). Collapsing *immediate* duplicates (not all duplicates document-wide — that could wrongly remove legitimately repeated content far apart) removes this specific noise pattern cheaply.

### Try it yourself

```bash
python -m src.ingestion.clean data/raw/AAPL/AAPL_10-K_2025-10-31.htm
```
This prints the first 3000 characters of cleaned text plus a total length — a fast way to sanity-check cleaning on one file before running it across all 90.

---

## Step 3 — Section-aware chunking (`src/chunking/chunker.py`)

This is the most intricate piece of engineering in Phase 1 — worth understanding deeply, not just copying.

### The problem this solves

You can't just slice a filing into fixed 700-token windows blindly. If a window happens to start mid-sentence in the middle of "Risk Factors" and end in the middle of "Legal Proceedings," you've created a chunk that's about *both* topics at once and *neither* topic well — retrieval quality drops because the chunk's embedding is a blurry average of two different meanings.

The fix: **split by real document section first** (Item 1A, Item 3, Item 7, etc.), *then* sub-chunk each section into token-sized pieces. A chunk never crosses a section boundary.

### Sub-problem: SEC filings list every section TWICE

Every 10-K has a **table of contents** near the top that lists "Item 1A. Risk Factors" as a line with a page number, and then the **actual section** appears later in the document with the same header text. A naive regex that finds `"Item 1A. Risk Factors"` and treats it as a section start will find it twice — once in the TOC (wrong, treat as noise) and once for real.

### The fix — a genuinely clever trick worth understanding

```python
HEADER_LINE_RE = re.compile(
    r"^Item\s+(\d{1,2}[A-Z]?)\.?\s+([A-Z][A-Za-z0-9 ,'&\-/]{2,150}?)\.?$"
)
```
This matches a whole line that looks like `Item 1A Risk Factors` or `Item 3. Legal Proceedings.` — anchored to the *entire line* (`^...$`) so it won't match the phrase appearing mid-sentence inside a paragraph (e.g., "as discussed in Item 1A above").

Then, instead of trying to cleverly detect "is this the TOC or the real section" directly, the code uses a much simpler and more robust trick:

```python
last_occurrence = {}
for idx, label in header_positions:
    num = re.match(r"Item (\d{1,2}[A-Z]?)", label).group(1)
    key = num  # dedupe by item number only
    last_occurrence[key] = (idx, label)   # overwrites earlier occurrences
```

**Why this works:** iterate through every header match in document order, and for each Item number, just keep *overwriting* a dictionary entry every time you see it again. By the time the loop finishes, `last_occurrence[num]` holds the *last* (i.e. real, non-TOC) occurrence of every item number — because the TOC always appears *before* the real section in the document, the real section's index always overwrites the TOC's. **You never had to explicitly detect "is this a TOC line" at all** — position order does the work for you. This is a good example of solving a messy heuristic problem by choosing a data structure (a dict that only keeps the last write) that makes the messy case naturally correct, instead of writing an explicit TOC-detector.

Once you have the real header positions, each section is just the document text between one header and the next:

```python
for j, (idx, label) in enumerate(ordered):
    start = idx + 1
    end = ordered[j + 1][0] if j + 1 < len(ordered) else len(lines)
    body = "\n".join(lines[start:end]).strip()
```

Then, within each section, token-based sliding-window sub-chunking:

```python
def chunk_section_text(section_text, min_tokens=500, max_tokens=800, overlap_tokens=100):
    tokens = ENCODING.encode(section_text)
    if len(tokens) <= max_tokens:
        return [section_text]

    chunks = []
    start = 0
    step = max_tokens - overlap_tokens   # = 700, i.e. 800 - 100
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunks.append(ENCODING.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += step
    return chunks
```

### Worked example — why 100-token overlap matters

Say a section is 1600 tokens. With `max_tokens=800`, `step=700`:
- Chunk 1: tokens 0-800
- Chunk 2: tokens 700-1500 (overlaps chunk 1 by 100 tokens, tokens 700-800)
- Chunk 3: tokens 1400-1600 (overlaps chunk 2 by 100 tokens)

**Why overlap at all:** imagine the single most important sentence in the section happens to fall exactly at token 795-810 — straddling the boundary between chunk 1 and chunk 2. Without overlap, that sentence gets cut in half, and *neither* chunk contains the complete idea, so neither chunk will retrieve well for a question about it. With 100 tokens of overlap, that sentence is fully intact inside chunk 2 (which starts at 700, well before it). Overlap is cheap insurance against the exact failure mode of "the important part got chopped at a boundary."

**Why 500-800 tokens specifically (not, say, 200 or 2000):** too small (e.g. 200 tokens) and a chunk often doesn't contain a complete thought — a risk factor description might need 3-4 sentences of context to make sense, and a tiny chunk fragments that. Too large (e.g. 2000+ tokens) and (a) the embedding becomes a blurry average of too many different sub-topics, hurting retrieval precision, and (b) you waste context window budget at generation time on mostly-irrelevant text within a "relevant" chunk. 500-800 is a well-established sweet spot in RAG practice — small enough to stay topically coherent, large enough to contain complete ideas.

### The real failure hit here — worth knowing before you hit it yourself

Financial-services 10-Ks (JPMorgan, and others) don't reliably continue with sequential `Item N` headers all the way to the end the way tech-company filings do — sometimes the last matched section (e.g. "Item 15 Exhibits") is followed by hundreds of pages of exhibits/signatures/schedules with no further `Item` header, so that final "section" swallowed ~985,000 characters in one go. The **sub-chunker still works correctly** underneath (it still slices that huge section into proper 500-800 token pieces) — but the *section label* metadata on all those chunks is coarse/misleading ("Item 15 Exhibits" attached to what's actually mostly boilerplate exhibit text). This is a real, documented limitation, not a crash — and it's exactly the kind of "what I found and how I'd fix it" story a case study wants: *the fix would be adding an end-of-document sentinel or exhibit-list detector to cap runaway sections.*

### Try it yourself

```bash
python -m src.chunking.chunker data/raw/JPM/JPM_10-K_2026-02-13.htm
```
Prints every detected section with its character/token count, plus total chunk count and token min/max/avg — the fastest way to eyeball whether your header regex is behaving before running the full 90-file batch.

---

## Step 4 — Master ingestion driver (`src/chunking/build_chunks.py`)

This ties steps 2 and 3 together across all 90 files and writes the final chunk dataset with full metadata.

### Chunk ID design

```python
def make_chunk_id(ticker, form, filing_date, section, idx):
    raw = f"{ticker}|{form}|{filing_date}|{section}|{idx}"
    h = hashlib.sha1(raw.encode()).hexdigest()[:10]
    return f"{ticker}_{filing_date}_{h}"
```

**Why hash instead of just a running integer counter:** a hash derived from the chunk's own identity (ticker + form + date + section + position) is deterministic and stable — if you re-run chunking after fixing a bug, chunks that didn't change get the *same* ID again. A simple incrementing counter would reassign different IDs to unrelated chunks on every re-run, breaking any external reference to a chunk_id (like a citation someone saved, or a test case referencing a specific chunk). This is a small design choice that pays off the moment you iterate more than once — which you always do.

### The metadata schema (this is the backbone of the whole system)

```python
record = {
    "chunk_id": chunk_id,
    "ticker": entry["ticker"], "sector": entry["sector"], "company": entry["company"],
    "form": entry["form"], "filing_date": entry["filing_date"], "section": section,
    "source_url": entry["source_url"], "local_path": local_path,
    "chunk_index_in_section": idx,
    "text": chunk["text"], "token_count": count_tokens(chunk["text"]),
    "allowed_roles": ["analyst", "compliance"],   # RBAC placeholder for Phase 3
}
```

**Why `allowed_roles` is added now, in Phase 1, even though RBAC filtering is a Phase 3 feature:** adding a metadata field is free at write time and expensive to retrofit later (it means re-processing all 10,027 chunks and re-embedding/re-indexing everything). Design your schema for where the project is *going*, not just where it is today — but only for fields that cost nothing now. This is different from building the actual RBAC filtering logic early (which would be wasted, premature work) — it's specifically about not closing a door that costs nothing to leave open.

**A real bug this file had to work around:** the `manifest.json` written during the original download stored `local_path` pointing at the *old* project folder (before the project was moved to its own standalone folder). The ingestion driver has a `resolve_local_path()` function that ignores the stale absolute path in the manifest and reconstructs the correct path from `PROJECT_ROOT / "data" / "raw" / ticker / filename` instead, falling back to the manifest's path only if that reconstruction fails. **Lesson: don't trust stored absolute paths across a project move — derive paths relative to a known root wherever possible.**

### Try it yourself

```bash
python -m src.chunking.build_chunks
```
Writes `data/chunks.jsonl` — one JSON object per line, 10,027 lines for this corpus. Prints per-file progress and a failure summary at the end (any files that couldn't be read/parsed).

---

## Step 5 — Embed + index into Qdrant (`src/indexing/build_qdrant_index.py`)

### Why a vector database at all

An embedding model turns text into a list of numbers (a vector) such that texts with *similar meaning* end up as *nearby vectors* in that number-space. "Apple discloses supply chain risk" and "AAPL identifies concentration risk in its manufacturing partners" are worded completely differently but mean similar things — their vectors will be close together. A vector database (Qdrant here) stores millions of these vectors and can quickly find "which stored vectors are closest to this query's vector" — that's semantic/meaning-based search.

### The actual code

```python
model = SentenceTransformer("BAAI/bge-base-en-v1.5")
embeddings = model.encode(texts, batch_size=32, normalize_embeddings=True)
```

**Why `BAAI/bge-base-en-v1.5` specifically:** it's a strong, free, open-weight embedding model that runs entirely on CPU at reasonable speed (no GPU required, no API cost), and it's specifically trained to be good at *retrieval* (finding relevant passages for a query) rather than general sentence similarity — that distinction matters, some embedding models optimize for a different task.

**Why `normalize_embeddings=True`:** this scales every vector to length 1. When vectors are normalized, cosine similarity (the standard way to compare "how close are two meanings") becomes just a dot product, which is faster to compute and numerically better-behaved. Qdrant is also configured with `Distance.COSINE` — the normalization and the distance metric choice need to match, or your similarity scores become meaningless.

**Why `bge` specifically needs a query instruction prefix** (see `retriever.py`'s `query_instruction`):
```yaml
query_instruction: "Represent this sentence for searching relevant passages: "
```
`bge` models are trained *asymmetrically* — the way you embed a search query and the way you embed a stored document are meant to be slightly different (the query gets this extra instruction text prepended, documents don't). Skipping this doesn't break anything outright, but it measurably weakens retrieval quality — this is a model-specific detail you have to know from the model's documentation, not something you'd guess.

### Local embedded Qdrant — what this actually means

```python
client = QdrantClient(path=qdrant_path)   # e.g. "./data/qdrant_db"
```

Normally Qdrant runs as a separate server process (Docker container, or a cloud cluster) that your code talks to over a network connection. **Local embedded mode skips all of that** — `qdrant-client` runs the entire vector database logic *inside your own Python process*, reading and writing directly to files on disk at the given path. No server, no signup, no API key, no network calls at all — the tradeoff is that only one process can have that folder open at a time (fine for a single-developer portfolio project, wrong choice for a real multi-user production service).

### Worked example: what a stored point looks like

```python
payload = {k: v for k, v in chunk.items() if k != "text"}
payload["text"] = chunk["text"]
points.append(PointStruct(id=i, vector=vec.tolist(), payload=payload))
```
Each point in Qdrant = one 768-number vector + a "payload" dict carrying *all* the original chunk metadata (ticker, section, source_url, allowed_roles, the text itself, etc.). This is what makes Qdrant results directly usable downstream — you get the actual chunk text and its citation metadata back in the same query, not just an ID you'd have to look up separately.

### Try it yourself

```bash
python -m src.indexing.build_qdrant_index
```
This is the slowest step in the whole pipeline on CPU — embedding 10,027 chunks takes real time (tens of minutes, hardware-dependent). It prints a progress bar during embedding and a final point count once indexing finishes.

---

## Step 6 — BM25 keyword index (`src/indexing/build_bm25_index.py`)

### Why you need this in addition to the vector index

Dense embeddings are excellent at *meaning* but can be weak on exact tokens — a specific dollar figure, a ticker symbol, an exact legal term. BM25 is a classic keyword-ranking algorithm (the same family of ideas search engines used for decades before embeddings existed): it scores documents by how often the query's exact words appear, weighted by how rare/specific those words are across the whole corpus. It will find "the exact page containing '$387 million'" reliably in a way embeddings sometimes won't, because embeddings can treat "$387 million" and "$385 million" as nearly the same point in vector space — BM25 won't.

```python
TOKEN_RE = re.compile(r"[A-Za-z0-9%$.]+")

def tokenize(text):
    return [t.lower() for t in TOKEN_RE.findall(text)]

corpus_tokens = [tokenize(c["text"]) for c in chunks]
bm25 = BM25Okapi(corpus_tokens)
```

**Why the tokenizer regex specifically includes `%`, `$`, `.`:** a generic word-tokenizer would strip these, turning "$387 million" into just "387" and "million" as separate tokens, losing the fact that it's a dollar figure. Keeping `$` and `%` attached to numbers preserves exactly the kind of precise financial-document tokens BM25 is supposed to be good at catching.

**Why store `chunk_ids` alongside the pickled BM25 model, not the full chunk text again:** the full chunk text already lives in `chunks.jsonl`. Storing it a second time inside the BM25 pickle would just duplicate ~40MB of data for no benefit — instead, the BM25 index only stores which chunk_id corresponds to which internal document index, and the retriever looks up full chunk records from `chunks.jsonl` by ID when needed. **Lesson: don't duplicate a large dataset across multiple index files just because it's convenient in the moment — index by ID and look up once.**

### Try it yourself

```bash
python -m src.indexing.build_bm25_index
```
Fast — seconds, not minutes, since BM25 needs no embedding model, just tokenization and frequency counting. Saves `data/bm25_index.pkl`.

---

## Step 7 — The retriever: RRF fusion + reranking (`src/retrieval/retriever.py`)

This is where dense search and keyword search actually get combined, and it's worth understanding the math, not just calling a function.

### Reciprocal Rank Fusion (RRF) — the actual algorithm

```python
def rrf_fuse(self, ranked_lists, k=60, top_k=20):
    rrf_scores = {}
    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            cid = item["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    fused_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)
    return fused_ids[:top_k]
```

**The formula:** for each chunk, its RRF score is `sum over every ranked list it appears in of 1 / (k + rank + 1)`, where `rank` is its position (0-indexed) in that list, and `k=60` is a constant.

### Worked example with real numbers

Suppose for the query "Apple supply chain risk," dense search returns Chunk A at rank 0 (1st place) and Chunk B at rank 5 (6th place). BM25 search returns Chunk B at rank 0 (1st place) and doesn't return Chunk A in its top-20 at all.

- Chunk A's RRF score: `1/(60+0+1)` from dense = `1/61 ≈ 0.0164`. (Not in BM25's list, contributes 0.)
- Chunk B's RRF score: `1/(60+5+1)` from dense `= 1/66 ≈ 0.0152`, **plus** `1/(60+0+1)` from BM25 `= 1/61 ≈ 0.0164`. Total ≈ `0.0316`.

**Chunk B wins the fusion (0.0316 > 0.0164) even though it ranked lower in the dense-only search**, because it ranked highly in *both* lists — RRF rewards chunks that multiple independent retrieval methods agree on, not just whichever method liked it most. This is exactly why hybrid search catches things vector-only search misses: a chunk that's a strong keyword match but a mediocre semantic match still gets pulled up if it's genuinely relevant.

**Why `k=60` specifically:** it's the standard default used in the original RRF paper and most production implementations. The constant dampens the effect of rank differences at the extremes (rank 1 vs rank 2 matters less than you'd think without it) — you generally don't need to tune this unless you have a specific reason to.

### The three retrieval modes, and why they exist as separate modes at all

```python
def retrieve(self, query, mode="rerank", role=None):
    if mode == "naive":
        return self.dense_search(query, top_k=5)   # vector-only, no BM25, no rerank
    dense_hits = self.dense_search(query)
    bm25_hits = self.bm25_search(query)
    fused = self.rrf_fuse([dense_hits, bm25_hits])
    if mode == "hybrid":
        return fused[:5]                             # + BM25/RRF, no rerank
    elif mode == "rerank":
        return self.rerank(query, fused)              # + cross-encoder rerank
```

**Why three modes exist as distinct code paths instead of just always running the best one:** this is *the entire point of the eval harness*. You need to run the exact same 30 questions through each of these three configurations separately and score each with RAGAS, so you get an honest, isolated measurement of what each added step actually contributed. If you only ever ran the best pipeline, you'd have no before/after story — which is the single most important chart in this whole portfolio project.

### The reranker

```python
def rerank(self, query, candidates, top_k=5):
    pairs = [(query, c["text"]) for c in candidates]
    scores = self.reranker.predict(pairs)   # cross-encoder scores each pair directly
    ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
    return ranked[:top_k]
```

**Why a reranker is a fundamentally different (and more accurate) tool than the embedding search that already happened:** dense/BM25 search both score a query against a document *independently* — the query gets embedded once, the document got embedded once (long ago, at indexing time), and you just compare those two pre-computed vectors. A cross-encoder instead looks at the query *and* the candidate document *together, at the same time*, and directly predicts a relevance score for that specific pair. This is much more accurate (it can catch subtle relevance signals two independently-computed embeddings would miss) but also much slower — you can't pre-compute it, it has to run fresh for every query against every candidate. That's exactly why it only runs on the *top-20* fused candidates, not all 10,027 chunks: cheap-but-approximate search first (dense+BM25) narrows the field, expensive-but-precise reranking second polishes the final top-5. This two-stage "retrieve cheap, rerank precise" pattern is standard in production search systems for exactly this cost/accuracy tradeoff reason.

### The RBAC filter, already wired (enforcement point matters)

```python
def dense_search(self, query, top_k=None, role=None):
    ...
    if role:
        query_filter = Filter(must=[FieldCondition(key="allowed_roles", match=MatchAny(any=[role]))])
    results = self.qdrant.query_points(..., query_filter=query_filter)
```

**Why the filter is applied *inside* the Qdrant query itself, not as a post-processing step after getting results back:** if you retrieved documents first and then filtered by role afterward, the LLM's context window budget could get wasted on documents that get thrown away, and worse — a bug in the post-filter step could let a disallowed document slip through to generation. Filtering *at the database query level* means a document a role isn't allowed to see is never even retrieved in the first place — it can't leak downstream because it was never in the candidate set. This is the correct enforcement point, and it's built into the retriever now (Phase 1) even though the *policy* (which roles see what) isn't fully fleshed out until Phase 3.

### Try it yourself

```bash
python -m src.retrieval.retriever "What are Apple's main risk factors related to litigation?"
```
Runs all three modes back-to-back on one question and prints the retrieved chunk IDs, tickers, sections, and scores for each — the fastest way to visually compare naive vs. hybrid vs. reranked results on a single query before running the full 30-question eval.

---

## Step 8 — The versioned prompt template (`config/prompt_templates.yaml`)

### Why YAML, not a Python string

```yaml
active_version: "v1"
templates:
  v1:
    system_prompt: |
      You are a due-diligence research copilot...
```

**Why keep the prompt in a config file instead of hardcoded inside `generate.py`:** two reasons. First, a prompt change is a *behavior* change to the system, just like a code change — putting it in its own file means it shows up cleanly in `git diff` as its own thing, separate from logic changes, which makes it easy to correlate "we changed the prompt on this date" with "eval scores moved on this date" (this is exactly what the CI regression gate in Phase 3 depends on). Second, `active_version: "v1"` with a `templates:` dict means you can add a `v2` prompt alongside `v1` without deleting it, run your eval suite against both, and keep whichever wins — cheap A/B testing of prompt wording, which you lose if the prompt is just a string buried in code.

### The two rules that do the real work

```yaml
CITATION RULES (mandatory):
- Every factual claim you make MUST be immediately followed by a citation tag
  in the form [chunk_id] referencing the exact context chunk that supports it.
- Do NOT cite a chunk unless it actually supports the specific claim next to it.
  A topically-related chunk that does not state the claim does not count as support.

OUT-OF-SCOPE / INSUFFICIENT CONTEXT RULE (mandatory):
- If the provided context does not contain enough information to answer the
  question, say so explicitly... Do NOT guess, and do NOT answer from general knowledge.
```

**Why this exact wording matters, tied back to the research:** recall the finding that 73% of RAG answers with citations were factually wrong despite having citations — this happens specifically because the LLM cites *something topically related* rather than something that actually *supports the exact claim*. The line "A topically-related chunk that does not state the claim does not count as support" is a direct, explicit attempt to instruct the model against that exact failure mode at the prompt level — this is the cheap, first line of defense; the faithfulness verifier in Phase 2 is the *enforced* version of this same idea (checking it's actually true, rather than just asking the model to try).

---

## Step 9 — Generation layer, CLI-first with API fallback (`src/generation/generate.py`)

```python
def _try_claude_cli(system_prompt, user_prompt, timeout=90):
    proc = subprocess.run(
        ["claude", "-p", full_prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()

def _try_anthropic_api(system_prompt, user_prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(model=HAIKU_MODEL, ...)
    ...
    return text

def generate_answer(question, chunks, version=None):
    answer = _try_claude_cli(...)
    if answer is None:
        answer = _try_anthropic_api(...)
    if answer is None:
        raise RuntimeError("GENERATION BLOCKED: both ... failed. Stopping rather than silently skipping.")
```

**Why raise a loud error instead of, say, returning an empty string or a placeholder when both paths fail:** a silent failure here is far more dangerous than a loud crash. If generation silently returned `""` or `"[no answer]"` on failure, that bad record could flow straight into the RAGAS eval step and get scored as a real (terrible) answer — corrupting your eval numbers without any obvious sign something went wrong. A loud `RuntimeError` stops the pipeline immediately, at the exact point of failure, with a clear message — this is a deliberate design principle: **fail loud and fast, never fail silent into corrupted downstream data.** This exact behavior is what happened during the real build — the CLI hit its usage limit on question 30 of 30, no API key was set, and the pipeline correctly stopped rather than silently producing a bad eval run.

**Why track cost even though it's "just $0.00 so far":** the cost-logging code (`_log_usage`) runs on every single call, whether it succeeds via CLI (cost recorded as $0, since the CLI doesn't expose token counts) or via the API fallback (real token counts and computed dollar cost recorded). This means at any point in the build, you can answer "how much have we actually spent" with a real number from a log file instead of a guess — which matters a lot when you've set yourself a hard $5 budget ceiling.

---

## Step 10 — 30 eval questions across 3 categories (`eval/questions.jsonl`)

```json
{"id": "q01", "category": "single_hop", "question": "What does Apple identify as a key risk factor related to its supply chain concentration?", "tickers": ["AAPL"]}
{"id": "q13", "category": "multi_hop", "question": "Compare the litigation risk disclosures of Johnson & Johnson and Bristol-Myers Squibb..."}
{"id": "q25", "category": "out_of_scope", "question": "What is the current weather forecast for New York City this week?"}
```

**Why three categories, not just "30 random questions":**
- **single_hop** (12 questions) — one fact, one company, one section. Tests baseline retrieval competence.
- **multi_hop** (12 questions) — comparisons across two companies/filings. This is the category the NIST research specifically found 73% of production RAG systems fail at. Without this category in your eval set, you'd never know if your system has this exact, documented, industry-wide failure mode — so this category isn't optional, it's the whole point of testing multi-hop at all.
- **out_of_scope** (6 questions) — weather, stock advice, unrelated companies (Tesla and Amazon aren't in the 30-company corpus). These test whether the refusal rule in the prompt template actually works, or whether the model hallucinates an answer anyway. A RAG system that never gets tested on questions it *should* refuse will never reveal whether its refusal instructions actually function.

**Why writing these can't really be automated well:** you need to actually know what's plausibly in a JPMorgan 10-K vs. a Pfizer 10-K to write questions that are answerable-but-hard, not trivially easy or impossible. This is closer to "writing a good exam" than "generating test data" — it requires domain understanding of the corpus you built.

---

## Step 11 — The RAGAS evaluation harness (`eval/run_ragas.py`)

### The four metrics, explained in plain terms

- **Faithfulness** — does the generated answer's claims actually follow from the retrieved context, or did the model add things not supported by what it was given? (This is the metric most directly measuring the citation-halo problem.)
- **Answer relevancy** — does the answer actually address the question asked, or does it wander off-topic?
- **Context precision** — of the chunks retrieved, how many were actually useful/relevant to answering the question? (Measures retrieval *quality*, not just recall.)
- **Context recall** — of the information actually needed to answer well, how much of it was present somewhere in the retrieved chunks? (Measures retrieval *completeness*.)

RAGAS computes all four by using an LLM as a judge internally — it decomposes the answer into individual claims, checks each against the retrieved context, and scores accordingly. This means **RAGAS itself makes many LLM calls per question** — often more calls than the original generation step, since it's doing multi-step reasoning about each answer.

### The clever part: wrapping Claude CLI as a RAGAS-compatible judge

RAGAS expects an LLM object following LangChain's interface. Since the project's Claude access is a custom CLI/API-fallback function, not a standard LangChain integration, the code writes a thin adapter class:

```python
class ClaudeJudgeLLM(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        prompt_text = "\n".join(m.content for m in messages if hasattr(m, "content"))
        out = _try_claude_cli(...)
        if out is None:
            out = _try_anthropic_api(...)
        if out is None:
            raise RuntimeError("RAGAS judge LLM: both claude CLI and API fallback failed.")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=out))])
```

**Why build this adapter instead of just using OpenAI or some default judge model RAGAS expects out of the box:** the whole point of the locked decision (CLI-first, API-fallback, budget-aware) was to keep the entire project's Claude usage under one unified, cost-tracked path. If RAGAS's judge silently used a *different* LLM/API path under the hood, you'd lose cost visibility and introduce a second, untracked spending channel — reusing the exact same `_try_claude_cli`/`_try_anthropic_api` functions for the judge means every single Claude call in the whole project, generation or evaluation, flows through one logged, budget-aware path.

Similarly, RAGAS also needs an embeddings model for some metrics — instead of using a paid embeddings API, the code reuses the *same local `bge-base-en-v1.5` model* already loaded for retrieval:

```python
class BgeEmbeddings(LCEmbeddings):
    def embed_documents(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()
```

**Why this matters for your budget:** RAGAS embedding calls happen many times per question. If this silently used a paid embeddings API instead of the free local model already sitting in memory, that's a second, invisible cost source stacking on top of the judge LLM calls — reusing the local model keeps this entirely free.

### Running all three configs

```bash
python -m eval.run_ragas --mode naive
python -m eval.run_ragas --mode hybrid
python -m eval.run_ragas --mode rerank
```
Each run: loads the 30 questions, runs the full retrieve→generate pipeline for that specific mode, saves the raw (question, answer, contexts) records to `eval/results/records_{mode}.json`, then scores everything with RAGAS and saves `eval/results/ragas_summary_{mode}.json`.

**Why save the raw records separately from the RAGAS scores, as two different files:** if RAGAS scoring crashes or you want to re-score with a different metric set later, you don't want to have to re-run the (slow, costly) generation step again — the raw records are the expensive, hard-to-reproduce-exactly artifact; RAGAS scoring against saved records is comparatively cheap to redo.

---

## Step 12 — The before/after chart (`eval/plot_results.py`)

```python
MODES = ["naive", "hybrid", "rerank"]
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
for i, mode in enumerate(MODES):
    vals = [summaries[mode].get(m, 0) for m in METRICS]
    ax.bar(x + (i - 1) * width, vals, width, label=MODE_LABELS[mode], color=colors[i])
```

A grouped bar chart — four metric groups on the x-axis, three bars (naive/hybrid/rerank) side by side within each group, so you can see at a glance whether each pipeline addition (hybrid search, then reranking) actually moved each metric up, and by how much. **This single PNG is the most important portfolio artifact from all of Phase 1** — it's the concrete, visual evidence of engineering judgment the research specifically flagged as what separates a real portfolio project from a tutorial-follow-along.

---

## Step 13 — The Streamlit demo (`app.py`)

```python
role = st.sidebar.selectbox("Query as role (RBAC placeholder)", ["analyst", "compliance"])
...
chunks = retriever.retrieve(question, mode="rerank", role=role)
result = generate_answer(question, chunks)
st.markdown(result["answer"])
for c in chunks:
    with st.expander(f"[{c['chunk_id']}] {c['ticker']} - {c['company']} ..."):
        st.write(f"**Source URL:** {c['source_url']}")
```

Straightforward: a text box, a "run query" button, the answer rendered as markdown (so citation tags and formatting show cleanly), and an expandable panel per retrieved chunk showing exactly which source it came from, its rerank score, and its allowed roles — this transparency (showing the actual retrieved evidence, not just the final answer) is itself a small trust-building feature worth keeping even after the app gets fancier.

The role selector in the sidebar is explicitly labeled as a placeholder with an explanatory note — it's wired into the retriever's `role=` parameter (real filtering already works, from Step 7), but the actual *policy* of who should see what isn't fleshed out until Phase 3. This is intentional, incremental scope — the mechanism exists now; the policy comes later.

---

## Recap — the full command sequence to build this from scratch

```bash
# 0. setup
python3.11 -m venv venv && source venv/bin/activate
pip install llama-index qdrant-client sentence-transformers rank_bm25 "ragas==0.2.15" streamlit pyyaml matplotlib beautifulsoup4 anthropic tiktoken lxml datasets langchain==0.3.30 langchain-community==0.3.31 langchain-core==0.3.86

# 1. download corpus (already done — see scripts/download_filings.py)

# 2-4. clean, chunk, write metadata
python -m src.chunking.build_chunks

# 5. embed + index dense
python -m src.indexing.build_qdrant_index

# 6. index sparse
python -m src.indexing.build_bm25_index

# 7. sanity-check retrieval on one question
python -m src.retrieval.retriever "your test question here"

# 8-9. (config files already written: config/prompt_templates.yaml, src/generation/generate.py)

# 10. (eval/questions.jsonl already written)

# 11. run eval three ways
python -m eval.run_ragas --mode naive
python -m eval.run_ragas --mode hybrid
python -m eval.run_ragas --mode rerank

# 12. chart
python -m eval.plot_results

# 13. demo
streamlit run app.py
```

Everything up through step 6 has been run and verified against this exact 30-company, 90-filing, 10,027-chunk corpus. Steps 11-13 are what's left — see `RESUME_BUILD.md` for the exact point they stopped at and how to finish them.
