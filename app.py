"""
RAG Due Diligence Copilot -- bare local Streamlit demo.

Runs the full best pipeline (hybrid + rerank) and shows the answer with
citations. Run locally with:

    source venv/bin/activate
    streamlit run app.py
"""
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.retrieval.retriever import Retriever
from src.generation.generate import generate_answer, build_prompt, format_context
from src.verification.faithfulness_verifier import verify_answer
from src.observability.trace import create_trace, _estimate_tokens

st.set_page_config(page_title="RAG Due Diligence Copilot", layout="wide")


@st.cache_resource
def get_retriever():
    return Retriever()


st.title("RAG Due Diligence Copilot")
st.caption(
    "M&A / equity-research due-diligence assistant over 90 SEC EDGAR filings "
    "(30 companies, 3 sectors). Pipeline: hybrid retrieval (BM25 + dense RRF) "
    "+ cross-encoder reranking + Claude generation with mandatory citations."
)

role = st.sidebar.selectbox(
    "Query as role",
    ["analyst", "compliance", "external_contractor"],
    index=0,
)
_roles_desc = {
    "analyst": "Internal analyst — full access to all indexed filings.",
    "compliance": "Superset role — everything plus retrieval audit logs.",
    "external_contractor": "Deal-specific contractor — financial-sector filings excluded.",
}
st.sidebar.caption(_roles_desc[role])
st.sidebar.markdown(
    "RBAC is enforced **at retrieval time** (Qdrant payload filter + BM25 "
    "pre-filter), not after generation. Every query is appended to "
    "`logs/retrieval_audit.jsonl` with the requesting role, eligible vs "
    "retrieved chunks."
)

question = st.text_input(
    "Ask a due-diligence question about the indexed filings",
    placeholder="e.g. Compare litigation risk between Johnson & Johnson and Bristol-Myers Squibb",
)

if st.button("Run query", type="primary") and question.strip():
    retriever = get_retriever()
    trace = create_trace(question, role=role, mode="rerank")
    with st.spinner("Retrieving (hybrid RRF) + reranking..."):
        with trace.span("retrieval", metadata={"role": role, "mode": "rerank"}):
            chunks = retriever.retrieve(question, mode="rerank", role=role)
            trace.spans[-1].metadata["eligible"] = retriever._count_eligible(role)
            trace.spans[-1].metadata["retrieved"] = len(chunks)

    with st.spinner("Generating answer..."):
        with trace.span(
            "generation",
            tokens_in=_estimate_tokens(format_context(chunks) + question),
            metadata={"role": role, "source": "opencode_cli"},
        ) as gen_span:
            try:
                result = generate_answer(question, chunks)
            except RuntimeError as e:
                trace.status = "error"
                trace.save()
                st.error(str(e))
                st.stop()
            gen_span.tokens_out = _estimate_tokens(result["answer"])
            gen_span.metadata["source"] = result["source"]

    st.subheader("Answer")
    st.caption(f"generated via: {result['source']}")
    st.markdown(result["answer"])

    with st.spinner("Verifying answer faithfulness..."):
        try:
            # Only attach an entity hint if every retrieved chunk is from the
            # same company -- attaching the wrong one for a cross-company
            # comparison question would be worse than attaching none.
            tickers = {c["ticker"] for c in chunks}
            entity = chunks[0]["company"] if len(tickers) == 1 else None
            with trace.span(
                "verification",
                tokens_in=_estimate_tokens(result["answer"]),
                metadata={"role": role},
            ) as ver_span:
                verification = verify_answer(result["answer"], [c["text"] for c in chunks], entity=entity)
                if verification.get("score") is not None:
                    ver_span.tokens_out = _estimate_tokens(str(verification["flagged_claims"]))
                    ver_span.metadata["score"] = verification["score"]
                    ver_span.metadata["total_claims"] = verification["total_claims"]
                    ver_span.metadata["refused_claims"] = verification["refused_claims"]
                    ver_span.metadata["pipeline_claims"] = verification["pipeline_claims"]
                ver_span.metadata["skipped"] = verification.get("skipped")
        except Exception as e:
            verification = {"pass": None, "error": str(e)}

    trace.save()
    st.sidebar.caption(
        f"last query: {trace.total_ms:.0f}ms total · "
        f"{trace.total_tokens_in + trace.total_tokens_out} tokens"
    )

    if verification.get("error"):
        st.info(
            "Faithfulness verification unavailable "
            f"(opencode CLI call failed: {verification['error']})"
        )
    elif verification.get("skipped"):
        st.info(
            "Answer is a refusal / retrieval-scope statement with no document claims — "
            "faithfulness check not applicable."
        )
    elif verification["pass"]:
        st.success(f"Faithfulness verified ({verification['score']:.0%} of claims supported)")
    else:
        st.warning(
            f"Some claims not fully supported ({verification['score']:.0%} verified). "
            "Review citations carefully."
        )
        if verification["flagged_claims"]:
            st.caption("Flagged claims:")
            for claim in verification["flagged_claims"]:
                st.caption(f"- {claim}")

    st.subheader("Retrieved chunks (top-5, reranked)")
    for c in chunks:
        with st.expander(
            f"[{c['chunk_id']}] {c['ticker']} - {c['company']} - {c['form']} "
            f"({c['filing_date']}) - {c['section']}"
        ):
            st.write(f"**Source URL:** {c['source_url']}")
            st.write(f"**Rerank score:** {c.get('rerank_score', 'n/a')}")
            st.write(f"**Allowed roles:** {c['allowed_roles']}")
            st.text(c["text"][:2000])
