"""
Generation layer: assembles the versioned prompt template with retrieved
context and calls Claude for an answer.

Per the locked decision:
  1. Try the `claude` CLI first (non-interactive, subprocess).
  2. If that fails/unavailable, fall back to the Anthropic API using
     ANTHROPIC_API_KEY (model: a Haiku-class model, cheapest available).
  3. If neither works, raise a clear, loud error -- never silently skip.

Also tracks token usage / estimated cost when the API fallback is used, so
actual spend can be reported (locked decision: stay well under $5).
"""
import json
import os
import subprocess
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Haiku-class pricing (per BUILD_SPEC.md Part 2): $0.25 / $1.25 per M tokens in/out.
HAIKU_MODEL = "claude-haiku-4-5"
HAIKU_PRICE_IN_PER_M = 1.00   # conservative estimate; exact rate checked in handover doc
HAIKU_PRICE_OUT_PER_M = 5.00

# Sonnet 5 intro pricing, valid through 2026-08-31 (then $3.00/$15.00 per M).
SONNET_MODEL = "claude-sonnet-5"
SONNET_PRICE_IN_PER_M = 2.00
SONNET_PRICE_OUT_PER_M = 10.00

_PRICE_TABLE = {
    HAIKU_MODEL: (HAIKU_PRICE_IN_PER_M, HAIKU_PRICE_OUT_PER_M),
    SONNET_MODEL: (SONNET_PRICE_IN_PER_M, SONNET_PRICE_OUT_PER_M),
}

_usage_log_path = PROJECT_ROOT / "logs" / "api_usage.jsonl"
_usage_log_path.parent.mkdir(parents=True, exist_ok=True)


def _log_usage(source: str, input_tokens: int, output_tokens: int, cost_usd: float):
    rec = {
        "ts": time.time(),
        "source": source,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    with open(_usage_log_path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def load_prompt_template(version: str = None) -> dict:
    config = yaml.safe_load(open(PROJECT_ROOT / "config" / "prompt_templates.yaml"))
    version = version or config["active_version"]
    return config["templates"][version]


def format_context(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        header = (
            f"[{c['chunk_id']}] ticker={c['ticker']} company={c['company']} "
            f"form={c['form']} filing_date={c['filing_date']} section={c['section']}"
        )
        parts.append(f"{header}\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def build_prompt(question: str, chunks: list[dict], version: str = None):
    tmpl = load_prompt_template(version)
    context_str = format_context(chunks)
    system_prompt = tmpl["system_prompt"]
    user_prompt = tmpl["user_prompt"].format(question=question, context=context_str)
    return system_prompt, user_prompt


def _try_opencode_cli(system_prompt: str, user_prompt: str,
                     model: str = "opencode/big-pickle",
                     timeout: int = 120) -> str | None:
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    try:
        proc = subprocess.run(
            ["opencode", "run", "-m", model, full_prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        _log_usage("opencode_cli", 0, 0, 0.0)  # opencode free models, no cost
        return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def _try_claude_cli(system_prompt: str, user_prompt: str, timeout: int = 90) -> str | None:
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    try:
        proc = subprocess.run(
            ["claude", "-p", full_prompt, "--output-format", "text", "--model", "haiku"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        _log_usage("claude_cli", 0, 0, 0.0)  # CLI doesn't expose token counts
        return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def _try_anthropic_api(system_prompt: str, user_prompt: str, model: str = HAIKU_MODEL, max_tokens: int = 1024) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        in_price, out_price = _PRICE_TABLE.get(model, (HAIKU_PRICE_IN_PER_M, HAIKU_PRICE_OUT_PER_M))
        cost = (in_tok / 1e6) * in_price + (out_tok / 1e6) * out_price
        _log_usage("anthropic_api", in_tok, out_tok, cost)
        return text
    except Exception as e:
        print(f"Anthropic API fallback failed: {e}")
        return None


def generate_answer(question: str, chunks: list[dict], version: str = None) -> dict:
    system_prompt, user_prompt = build_prompt(question, chunks, version)

    # 1. Try opencode with big-pickle (free, primary)
    answer = _try_opencode_cli(system_prompt, user_prompt)
    source = "opencode_cli"

    # 2. Fallback to Claude Haiku CLI (free, secondary)
    if answer is None:
        answer = _try_claude_cli(system_prompt, user_prompt)
        source = "claude_cli"

    # 3. Last resort: Anthropic API (paid, tertiary)
    if answer is None:
        answer = _try_anthropic_api(system_prompt, user_prompt)
        source = "anthropic_api"

    if answer is None:
        raise RuntimeError(
            "GENERATION BLOCKED: all three sources failed (opencode unavailable, "
            "`claude` CLI not usable/not authenticated, and Anthropic API key "
            "not set or API call failed). Stopping rather than silently skipping."
        )

    return {
        "question": question,
        "answer": answer,
        "source": source,
        "chunk_ids_used": [c["chunk_id"] for c in chunks],
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.retrieval.retriever import Retriever

    r = Retriever()
    q = sys.argv[1] if len(sys.argv) > 1 else "What are Apple's main risk factors related to litigation?"
    chunks = r.retrieve(q, mode="rerank")
    result = generate_answer(q, chunks)
    print(f"[source={result['source']}]\n")
    print(result["answer"])
