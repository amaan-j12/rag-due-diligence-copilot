# Generation & Eval Rules

**Effective:** 2026-09-16 (updated in Phase 3)  
**Context:** Verified in Phase 3. Opencode flagship reasoning model
(`opencode/big-pickle`) is the most powerful free option and is now the
primary backend for both generation and verification. The old
`opencode/deepseek-v4-flash-free` model no longer resolves (server error),
and claude CLI hit its session limit (1:20am reset) during Phase 3 — both
retired as primary. API reserved for emergencies only.

---

## Primary Rule: Cost-First Generation

### Generation Priority (Generation Tasks)
```
1. ✅ opencode/big-pickle               (free, most powerful reasoning model)
2. ⚠️ claude haiku CLI                   (free fallback; subject to session limit)
3. ❌ Anthropic API                       (FORBIDDEN - only if explicit user approval)
```

**Why:** Opencode big-pickle is free and the strongest model available
through it. Claude CLI is a proven free fallback but shares a session quota.
Never use paid API without explicit user request.

### Verifier/Judge Priority (Eval Tasks)
```
1. ✅ opencode/big-pickle               (free, handles structured JSON + claim typing)
2. ⚠️ claude haiku CLI                   (free fallback; subject to session limit)
3. ❌ Anthropic API                       (FORBIDDEN - only if explicit user approval)
```

**Why:** Same as generation. The Phase 3 eval run used big-pickle with
0/42 call failures and 100% catch rate.

---

## Code Implementation

### In `src/generation/generate.py`

```python
def generate_answer(question: str, chunks: list[dict], version: str = None) -> dict:
    system_prompt, user_prompt = build_prompt(question, chunks, version)

    # 1. Try opencode big-pickle (free, primary)
    answer = _try_opencode_cli(system_prompt, user_prompt, model="opencode/big-pickle")
    source = "opencode_cli"

    # 2. Fallback to Claude Haiku CLI (free, secondary)
    if answer is None:
        answer = _try_claude_cli(system_prompt, user_prompt)
        source = "claude_cli_haiku"

    # 3. NEVER auto-fallback to API
    if answer is None:
        raise RuntimeError(
            "GENERATION BLOCKED: opencode/big-pickle and claude CLI both failed. "
            "API is disabled by cost-first rule. Fix the issue or request explicit API approval."
        )

    return {...}
```

### In `eval/run_ragas.py`

```python
class ClaudeJudgeLLM(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        prompt_text = "\n".join(m.content for m in messages if hasattr(m, "content"))
        system = "You are a precise evaluation assistant. Output only valid JSON."

        # 1. Try opencode big-pickle (free, primary)
        out = _try_opencode_cli(system, prompt_text, model="opencode/big-pickle")

        # 2. Fallback to Claude Haiku CLI (free, secondary)
        if out is None:
            out = _try_claude_cli(system, prompt_text)

        # 3. NEVER auto-fallback to API
        if out is None:
            raise RuntimeError(
                "RAGAS JUDGE BLOCKED: opencode/big-pickle and claude CLI both failed. "
                "API is disabled by cost-first rule. Fix or request explicit approval."
            )

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=out))])
```

---

## When API is Allowed

**Only in these cases:**
1. **Explicit user request:** "Use the API for this" or "ignore cost rules"
2. **Both free options exhausted:** Opencode + Claude CLI both provably unavailable
3. **Emergency only:** Production outage requires immediate fallback

**When requesting API:** Always get user confirmation first, never auto-escalate.

---

## Cost Tracking

- Log every call: `src/generation/generate.py` line 43–52 (`_log_usage()`)
- Track by source: opencode_big_pickle (0.0), claude_cli (0.0), anthropic_api ($$)
- Review monthly: `logs/api_usage.jsonl`

---

## Fallback Behavior

**Opencode timeouts or fails:**
- Wait 2 sec, retry once (line 89 in run_ragas.py)
- If still failing, immediately try Claude CLI
- Log the failure for debugging

**Claude CLI timeouts or fails:**
- Try once, then raise error (don't fallback to API)
- User must fix or approve API

**Both fail:**
- Clear error message with actionable next steps
- No silent failures, no API escalation

---

## Known Limitations & Workarounds

| Issue | Workaround | Cost |
|-------|-----------|------|
| Opencode JSON format mismatch (RAGAS) | Use Claude CLI fallback (already in code) | $0 |
| Opencode timeout (rare) | Automatic retry, then CLI fallback | $0 |
| Claude CLI rate limit (>100 calls/hr) | Space calls 1–2 sec apart, or use API | $0 (or $$) |
| Qdrant DB locking | Run RAGAS sequentially (already done) | $0 |

---

## Testing the Rules

Before each session, verify:

```bash
# Test generation priority
python -c "
from src.generation.generate import generate_answer
chunks = [{'chunk_id': 'test', 'text': 'test context', 'ticker': 'AAPL', 'company': 'Apple', 'form': '10-K', 'filing_date': '2025-01-01', 'section': 'Risk'}]
result = generate_answer('What is Apple?', chunks)
print(f'Source: {result[\"source\"]}')  # Should be 'opencode_cli' or 'claude_cli', never 'anthropic_api'
"

# Test RAGAS judge priority (check log output)
python eval/run_ragas.py --mode naive 2>&1 | grep -i "opencode\|claude_cli\|anthropic"
```

---

## Approved by

**User:** mdamaanco13@gmail.com  
**Date:** 2026-09-16 (Phase 3 update)  
**Reason:** opencode/deepseek legacy model retired (server errors); opencode/big-pickle verified free, reliable (0/42 call failures), and most powerful. Cost-first rule keeps expenses at $0.

**Next review:** After Phase 3 CI eval gate lands.
