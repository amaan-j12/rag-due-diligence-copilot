"""
Lightweight local observability layer (BUILD_SPEC.md Part 8).

Records a structured trace tree per query with per-stage timing, token
estimates, and estimated cost in USD — written to logs/traces.jsonl.

This mirrors the Langfuse trace structure (query → RBAC filter → retrieval
→ rerank → generation → verification) without requiring an external
observability service or API keys.  The JSONL output can be ingested by
Langfuse, LangSmith, or any log-pipeline for the case study screenshots.

Cost estimation uses a configurable pricing table (default: opencode free
models at $0.00 actual cost; Haiku-equivalent rates available for the
"what this would cost at production API pricing" tradeoff chart).
"""
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACES_DIR = PROJECT_ROOT / "logs"
TRACES_PATH = TRACES_DIR / "traces.jsonl"

# Haiku-class API-equivalent pricing for the cost-vs-accuracy tradeoff
# chart (BUILD_SPEC.md Part 8).  Opencode free models actual cost = $0,
# but for the portfolio narrative we estimate what the same pipeline would
# cost running through a paid Haiku API endpoint.
_PRICING = {
    "haiku": {"input_per_m": 1.00, "output_per_m": 5.00},
    "free":  {"input_per_m": 0.00, "output_per_m": 0.00},
}

# Which pricing tier to use for the "estimated" cost column.
# Switch to "haiku" when generating the tradeoff chart; keep "free" for
# live tracing (actual cost).
_DEFAULT_COST_TIER = "free"


def _estimate_tokens(text: str) -> int:
    """Approximate token count using tiktoken cl100k_base (the same
    encoding used by OpenAI / Anthropic models).  Falls back to a rough
    heuristic if tiktoken isn't available."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # ~4 chars/token rough heuristic
        return len(text) // 4


def _estimate_cost(tokens_in: int, tokens_out: int, tier: str = _DEFAULT_COST_TIER) -> float:
    """Estimate USD cost from token counts using the given pricing tier."""
    prices = _PRICING.get(tier, _PRICING["free"])
    return (tokens_in / 1e6) * prices["input_per_m"] + (tokens_out / 1e6) * prices["output_per_m"]


@dataclass
class Span:
    name: str
    start_s: float
    end_s: float = 0.0
    duration_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str
    question: str
    role: str
    mode: str
    spans: list[Span] = field(default_factory=list)
    start_s: float = field(default_factory=time.monotonic)
    status: str = "ok"
    total_ms: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0

    @contextmanager
    def span(self, name: str, tokens_in: int = 0, tokens_out: int = 0,
             cost_usd: float = 0.0, **metadata):
        s = Span(name=name, start_s=time.monotonic(),
                 tokens_in=tokens_in, tokens_out=tokens_out,
                 cost_usd=cost_usd, metadata=metadata)
        try:
            yield s
        except Exception as e:
            s.metadata["error"] = str(e)
            self.status = "error"
            raise
        finally:
            s.end_s = time.monotonic()
            s.duration_ms = round((s.end_s - s.start_s) * 1000, 1)
            self.spans.append(s)
            self.total_tokens_in += s.tokens_in
            self.total_tokens_out += s.tokens_out
            self.total_cost_usd += s.cost_usd

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "question": self.question,
            "role": self.role,
            "mode": self.mode,
            "status": self.status,
            "total_ms": round((time.monotonic() - self.start_s) * 1000, 1),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "cost_usd": round(s.cost_usd, 6),
                    **s.metadata,
                }
                for s in self.spans
            ],
        }

    def save(self):
        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        rec = self.to_dict()
        with open(TRACES_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec


def create_trace(question: str, role: str = "analyst", mode: str = "rerank") -> Trace:
    return Trace(
        trace_id=uuid.uuid4().hex[:12],
        question=question,
        role=role,
        mode=mode,
    )


def load_traces(path: Path | str | None = None) -> list[dict]:
    path = path or TRACES_PATH
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def summarize_traces(traces: list[dict]) -> dict:
    """Aggregate P50/P95 latency and $/query across a trace log."""
    import statistics

    if not traces:
        return {"n": 0}

    by_stage: dict[str, list[float]] = {}
    total_ms_list = []
    total_cost_list = []

    for t in traces:
        total_ms_list.append(t["total_ms"])
        total_cost_list.append(t.get("total_cost_usd", 0.0))
        for s in t.get("spans", []):
            by_stage.setdefault(s["name"], []).append(s["duration_ms"])

    def p50p95(vals):
        if not vals:
            return 0, 0
        return round(statistics.median(vals), 1), round(sorted(vals)[int(len(vals) * 0.95)], 1)

    stages = {}
    for name, vals in by_stage.items():
        med, p95 = p50p95(vals)
        stages[name] = {"n": len(vals), "p50_ms": med, "p95_ms": p95, "total_ms": round(sum(vals), 1)}

    overall_p50, overall_p95 = p50p95(total_ms_list)
    return {
        "n": len(traces),
        "total_p50_ms": overall_p50,
        "total_p95_ms": overall_p95,
        "avg_cost_per_query": round(sum(total_cost_list) / len(total_cost_list), 6) if total_cost_list else 0.0,
        "total_cost_usd": round(sum(total_cost_list), 6),
        "stages": stages,
    }