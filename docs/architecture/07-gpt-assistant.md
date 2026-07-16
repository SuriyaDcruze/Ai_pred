# Volume 07 — Conversation / GPT Assistant

## Purpose
The **conversational intelligence layer** — the natural-language interface that lets a
user talk to Aegis like an experienced analyst. It understands intent, calls the right
engines, and explains results. **It never predicts.**

## Status: 🟡 Partial — `app/chat/assistant.py` (rule-based), `app/chat/llm.py` (optional LLM)

## The one hard rule
> The LLM is architecturally **incapable** of producing a market prediction. It only
> reads structured outputs from the Prediction/Outcome/Intelligence engines and turns
> them into language. If an LLM ever emits Direction/Probability/Entry/Target, that is a
> critical architecture violation.

This is enforced by contract: the assistant's tools are *read-only calls* to Aegis
services (`/intelligence`, `/outcome`, `/sectors`, `/screener/nse`, `/news`), whose
responses originate from the models. The LLM composes explanations over those responses.

## Responsibilities
- **Intent recognition:** map "should I buy Reliance?", "compare TCS and Infosys", "I have
  ₹3L build a portfolio", "why WAIT?", "teach me risk management" → the right engine(s).
- **Orchestration:** call the engines, collect structured results.
- **Explanation:** produce clear, honest, analyst-grade prose over the structured data.
- **Teaching:** explain financial/TA concepts (patterns, R:R, calibration).
- **Memory:** remember user preferences (via User Profile, Vol 16) — never invent facts.

## Architecture (target)
```
User message
  → Intent classifier (LLM function-calling / tool-routing)
  → Tool calls to Aegis engines (read-only, structured)
  → LLM composes explanation grounded ONLY in tool results
  → Response (+ persistent disclaimer)
```
Current: `TradingAssistant` (deterministic intent + templated plans) with an optional
`LLMAssistant` (Claude) behind a config flag. Target: promote the LLM to a proper
tool-routing orchestrator with a fixed, read-only tool schema.

## Inputs / Outputs
- **In:** user text, current symbol/timeframe, (optional) tapped price, user profile.
- **Out:** natural-language reply + any structured plan echoed from the engines (entry/
  stop/target come from Risk, never from the LLM).

## API integration
- `/chat` (POST). Target: the endpoint injects a **read-only tool set**; the model cannot
  call anything that computes a prediction.

## Failure / logging
- LLM unavailable → deterministic assistant fallback (already implemented).
- Tool call fails → the assistant says so honestly; it does not hallucinate a number.

## Testing
- `tests/test_chat.py` — endpoint contract, that trade plans come from the engines.
- Target: guard test asserting the LLM tool schema contains **no** prediction-producing
  tool.

## Security
- Prompt-injection: the LLM must treat market data & news text as untrusted content, never
  as instructions. Disclaimers are appended server-side, not left to the model.

## Prediction-Model integration
- Strictly a consumer. Every claim it makes about a trade traces to an engine response.

## Future
- Multi-turn memory, "what changed since yesterday" (needs Historical Memory, Vol 13),
  comparison flows (TCS vs INFY), portfolio conversations (Vol 11).
