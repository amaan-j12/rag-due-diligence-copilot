"""
Faithfulness verifier: checks whether a generated answer's claims are
entailed by the retrieved context chunks it cites.

Backend is the opencode CLI (free models, DeepSeek) running as a
subprocess -- switched back from claude CLI after hitting Claude Code's
session limit. No separate auth/batching handoff needed.

One LLM call per verification (claim extraction + entailment combined into
a single prompt -- originally two calls, merged to cut subprocess-spawn
overhead roughly in half after latency testing showed ~96s/verification).

Claims are tagged by TYPE during extraction (Phase 3):
  - "document"  -> a factual claim about what a filing discloses. Scored
                   against the context via entailment.
  - "pipeline"  -> a statement about the retrieval/pipeline itself (e.g.
                   "the retrieved context contains no X for company Y").
                   Reported but NOT scored: there's nothing in the documents
                   to entail against, by definition.
  - "refusal"   -> an explicit scope refusal segment. Reported but NOT
                   scored.
This fixes two previously-documented limitations (both from
eval/verification_report.md):
  1. Mixed answers that combine a real, checkable answer for one company
     with a legitimate refusal for another are no longer skipped wholesale
     -- the real claims get scored, the refused portion is reported
     separately.
  2. Retrieval-composition meta-claims ("the retrieved context contains no
     quantitative data for Morgan Stanley") no longer read as unsupported
     document claims and drag the score down.

Pure refusals (refusal language and no [chunk_id] citation markers anywhere
in the answer) are still detected before calling the LLM and skipped:
document-entailment checking doesn't apply to a meta-statement about the
system's own scope, and running it anyway produced false "unfaithful" flags
on otherwise-correct refusals.
"""
import json
import re
import time

from src.generation.generate import _try_opencode_cli

# Most powerful opencode model available (flagship reasoning model).
VERIFIER_MODEL = "opencode/big-pickle"

# Heuristic refusal detection. Deliberately keyword-based rather than
# LLM-judged: the LLM judge itself was the thing producing false positives
# on refusals (it would extract "this system only answers X" as a claim and
# then fail to find it "entailed" in irrelevant retrieved context), so a
# second LLM call can't be trusted to self-detect this case reliably.
#
# Patterns are taken verbatim from observed refusal phrasing (generate.py's
# prompt template consistently produces these), not guessed -- a first
# attempt using generic phrases like "outside the scope" false-positived on
# a legitimate answer that happened to say "...chips outside the scope of
# the [export] controls...". Only match self-referential "I/this system"
# framing, never bare topical words like "scope" or "information" alone.
_REFUSAL_PATTERNS = [
    "i can't answer this",
    "i can't answer that",
    "i don't have enough information in the retrieved filings",
    "this system only answers questions grounded in",
    "does not provide investment advice",
    "does not provide personal investment advice",
    "does not provide guidance on",
    "cannot provide guidance on",
]


def is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(p in lowered for p in _REFUSAL_PATTERNS)


_CITATION_RE = re.compile(r"\[[A-Z]{1,6}_\d{4}-\d{2}-\d{2}_[0-9a-f]{10}\]")


def _has_citations(answer: str) -> bool:
    """Detect [chunk_id] citation markers. A pure refusal answers without
    citing anything; a mixed real+refusal answer cites the real portion."""
    return bool(_CITATION_RE.search(answer))


_CLAIM_KEYS = {"claim", "type", "verdict", "reason"}


def _typed_verdicts(verdicts: list) -> tuple[list, list, list]:
    """Partition parsed claim dicts by their tagged type.

    Backward-compat: a verdict dict without a "type" key (old prompt output)
    is treated as a "document" claim -- entailment checking applies.
    Returns (document_claims, refused_claims, pipeline_claims).
    """
    document, refused, pipeline = [], [], []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        t = v.get("type")
        if t == "pipeline":
            pipeline.append(v)
        elif t == "refusal":
            refused.append(v)
        else:  # missing type or "document"
            document.append(v)
    return document, refused, pipeline


COMBINED_PROMPT = """You will be given an ANSWER and a CONTEXT (retrieved \
document excerpts). Do this in two steps, but only output the result of \
step 2.

STEP 1 (internal): Break the ANSWER into self-contained statements. For each \
statement, decide its TYPE:
- "document": a factual claim about what a company/filing discloses.
- "pipeline": a claim about the retrieval/pipeline itself rather than the \
documents -- statements like "the retrieved context contains no X for company Y", \
"the excerpts are limited to the cover page", "no retrieved chunk states that Z", \
"the context does not let me characterize V in depth".
- "refusal": an explicit scope refusal -- statements like "I don't have enough \
information in the retrieved filings", "I cannot complete the requested comparison", \
"this system only answers questions grounded in".

Keep clauses of the same sentence together as ONE claim if splitting them would \
make either half meaningless on its own. NEVER phrase a claim as "the answer \
discusses/discloses/mentions X" -- state the actual fact X directly. Ignore \
formatting, citation markers like [chunk_id], and filler sentences that assert \
nothing (e.g. "Sources:"). Prefer fewer, complete claims over many fragments.

STEP 2 (output this): For each claim from step 1, output one JSON object with \
keys "claim", "type" ("document", "pipeline", or "refusal"), "verdict", and \
"reason". For "pipeline" and "refusal" claims set "verdict" to "n/a" -- they \
are NOT document claims, so entailment checking does not apply. Only \
"document" claims get a real verdict.

Entailment rule for "document" claims: mark "entailed" if the context supports \
the claim -- including reasonable paraphrases, summaries, or rewordings. You \
do not need a verbatim match; if a person reading the context would agree the \
claim is a fair restatement of it, that counts as entailed. Mark "not_entailed" \
ONLY if the context contradicts the claim, or is silent on the claim's subject \
entirely. Do not mark a claim not_entailed just because it adds mild \
interpretation, mild severity language (e.g. "significant", "key"), or restates \
the same idea in different words.

The CONTEXT is excerpted from companies' SEC filings and will typically \
self-refer as "the Company", "we", or "our" rather than naming itself. If a \
claim names a specific company and that matches the filing the context is drawn \
from, treat "the Company"/"we"/"our" as referring to that named company -- do \
not mark a claim not_entailed merely because the company's name isn't spelled \
out verbatim in the context.

Output ONLY a JSON array of objects, one per claim: [{{"claim": "...", "type": \
"document|pipeline|refusal", "verdict": "entailed|not_entailed|n/a", "reason": \
"one short sentence"}}]. If the ANSWER has no statements at all, output []. \
Nothing else.

CONTEXT:
{context}

ANSWER:
{answer}

JSON array:"""


def _parse_json_array(text: str):
    """Best-effort extraction of a JSON array from LLM output that may
    include leading/trailing prose or markdown code fences."""
    if text is None:
        return None
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _call_with_retry(system: str, prompt: str, attempts: int = 2, backoff_s: float = 3.0) -> str | None:
    """opencode CLI calls fail transiently under back-to-back sequential load
    (observed empirically: isolated calls succeed, tight loops sometimes
    don't). One retry with a short backoff clears most of these."""
    for i in range(attempts):
        out = _try_opencode_cli(system, prompt, model=VERIFIER_MODEL)
        if out:
            return out
        if i < attempts - 1:
            time.sleep(backoff_s)
    return None


def check_claims(answer: str, context: str, entity: str | None = None) -> list[dict] | None:
    if entity:
        context = f"[This context is excerpted from {entity}'s SEC filing.]\n\n{context}"
    prompt = COMBINED_PROMPT.format(context=context, answer=answer)
    out = _call_with_retry("You output only valid JSON.", prompt)
    verdicts = _parse_json_array(out)
    if not isinstance(verdicts, list):
        return None
    return verdicts


def verify_answer(answer: str, contexts: list[str], entity: str | None = None) -> dict:
    """
    Check if answer's claims are entailed by contexts.

    entity: optional company/ticker name the contexts belong to (e.g.
    "Apple" or "AAPL"). SEC filing text self-refers as "the Company"/"we",
    so without this the judge often can't confirm a claim's named company
    matches the context's filer. Pass it whenever known.

    Returns:
        {
            "pass": bool,             # True if all document-claims entailed
                                      #   (or nothing checkable -> skipped)
            "score": float | None,    # fraction of document claims entailed
            "flagged_claims": list[str],
            "total_claims": int,      # number of document claims,
            "refused_claims": int,    #   claims tagged as scope refusals
            "pipeline_claims": int,   #   claims about the retrieval/pipeline
            "latency_ms": float,
            "skipped": bool,          # pure refusal (no citations) or no doc claims
            "error": str | None,
        }
    """
    start = time.monotonic()
    latency = lambda: round((time.monotonic() - start) * 1000, 1)

    # Fast path: a PURE refusal — refusal language and no chunk citations —
    # has no document claims to check. Don't burn an LLM call on it.
    # A MIXED answer (refusals + citations for the substantive portion) falls
    # through to the LLM so the real claims still get verified.
    if is_refusal(answer) and not _has_citations(answer):
        return {
            "pass": True, "score": None, "flagged_claims": [],
            "total_claims": 0, "refused_claims": 0, "pipeline_claims": 0,
            "latency_ms": latency(), "skipped": True, "error": None,
        }

    context = "\n\n---\n\n".join(contexts)
    verdicts = check_claims(answer, context, entity=entity)
    if verdicts is None:
        return {
            "pass": False, "score": 0.0, "flagged_claims": [],
            "total_claims": 0, "refused_claims": 0, "pipeline_claims": 0,
            "latency_ms": latency(), "skipped": False,
            "error": "verification call failed (opencode CLI call failed or returned invalid JSON)",
        }

    document, refused, pipeline = _typed_verdicts(verdicts)
    n_refused = len(refused)
    n_pipeline = len(pipeline)

    if not document:
        # Nothing checkable: answer was entirely refusals / meta-statements
        # about the retrieval (e.g. "the retrieved context contains no X").
        # Not a failure — nothing to entail.
        return {
            "pass": True, "score": None, "flagged_claims": [],
            "total_claims": 0, "refused_claims": n_refused,
            "pipeline_claims": n_pipeline,
            "latency_ms": latency(), "skipped": True, "error": None,
        }

    flagged = [
        v.get("claim", "") for v in document
        if v.get("verdict") != "entailed"
    ]
    score = 1.0 - (len(flagged) / len(document))

    return {
        "pass": score >= 0.80,
        "score": round(score, 4),
        "flagged_claims": flagged,
        "total_claims": len(document),
        "refused_claims": n_refused,
        "pipeline_claims": n_pipeline,
        "latency_ms": latency(),
        "skipped": False,
        "error": None,
    }
