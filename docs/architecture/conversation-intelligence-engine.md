# Conversation Intelligence Engine — as built (Sprint 6, Volume 07, `v0.6.0`)

> A **read-only conversational explanation layer** over the completed Decision Intelligence Engine
> (Sprint 5). A user asks, in natural language, to **explain** an existing prediction / its evidence /
> confidence / historical behaviour / similar cases / learning / system status; the LLM **explains
> only** — it never predicts, retrains, recalculates confidence, gives advice, modifies data, or
> hallucinates. Frozen at `v0.6.0`, tag `v0.6.0-conversation-intelligence`.

> ⚠️ **Disambiguation.** Distinct from the legacy `app/chat/` (`TradingAssistant` / `LLMAssistant`),
> a pre-Decision-Intelligence rule-based + optional-LLM assistant exposed at the exact route
> `POST /chat`. The Sprint 6 engine is the **new** package `app/conversation/`; it consumes **only**
> the Decision Intelligence Engine and owns the `/chat/*` **sub-namespace** (message endpoint
> `POST /chat/message`). The legacy chat is untouched. (ADR 0029/0030.)

## Data flow / component interaction (the requested chain)
```
  User → Conversation Engine (M5) → Intent Detection (M2) → Retrieval Orchestrator (M3)
       → Decision Intelligence Engine (/intelligence/*, Sprint 5)   [→ Memory · Similarity · Learning]
       → Prompt Builder (M4) → LLM Adapter (M6, explanation only) → User Response
                                          ▲
                              /chat/* REST API (M7, thin transport)
```
The layer talks **only** to Decision Intelligence — never the prediction models — and imports
neither the Prediction nor the Outcome engine (AST-guarded).

## Package structure & public interfaces
`app/conversation/` (peer of `app/decision_intelligence/`):
- **`models.py` (M1, `cnv-1`)** — the conversation domain model: `Message`, `ConversationSession`
  (frozen, functional-update, SHA-256 checksum), `ConversationContext`, `Citation`; `Role` /
  `ConversationStatus` / `Availability` enums; deterministic ids; serialization.
- **`intent.py` (M2, `int-1`)** — the **Intent Detection Engine**: `IntentClassifier` (rule-based —
  phrase/keyword/synonym tiers + priority; **no LLM/embeddings**); extensible `INTENT_REGISTRY`;
  deterministic entity extraction + validation; `IntentClassification` (classification confidence =
  rule-match strength, **not** DI confidence).
- **`retrieval.py` + `sources.py` (M3, `ret-1`)** — the **Retrieval Orchestrator**: retrieves **only
  through** Decision Intelligence via a transport-independent `DecisionIntelligenceSource`
  (`InProcessSource` concrete); intent→target routing; deterministic pipeline; availability
  (`AVAILABLE`/`INSUFFICIENT_DATA`/`NOT_AVAILABLE`/`NOT_SUPPORTED`/`ERROR`); context merge (verbatim).
- **`prompt.py` (M4, `prm-1`)** — the **Prompt Builder**: fixed 10-section order; explain-only system
  prompt + per-intent templates; verbatim context; deterministic citation formatter (+ missing-
  citation rejection); deterministic token budget (trims lowest-priority first, always keeps system /
  user / citations). **Never invents or modifies content.**
- **`engine.py` (M5, `eng-1`)** — the **Conversation Engine**: in-memory session manager; lifecycle
  state machine (`CREATED`/`ACTIVE`/`WAITING_FOR_INPUT`/`COMPLETED`/`EXPIRED`/`ERROR`, validated);
  message pipeline; multi-turn memory (subject recall + pending-intent resume); follow-up manager
  (`CONTINUATION`/`CLARIFICATION_REQUIRED`/`MISSING_ENTITY`/`COMPLETED`); deterministic context
  window. Coordinates M1–M4 **without** executing retrieval, prompts, or an LLM.
- **`llm_adapter.py` (M6, `llm-1`)** — the **LLM Adapter**: provider-independent (`LLMProvider` ABC +
  `LLMAdapter`); registry + factory; `EchoProvider` (deterministic offline stub) + `OpenAIProvider` /
  `azure_openai` (duck-typed client, **no SDK import**); normalised `LLMRequest`/`LLMResponse`/
  `LLMError`; deterministic error categories, never leaked.
- **`app/api/chat.py` (M7)** — the thin `/chat/*` REST transport (6 endpoints).

## REST API (`/chat/*`, M7, ADR 0034)
| Method · Path | Purpose |
|---|---|
| `POST /chat/message` | run the full pipeline for a user message → assistant response + citations + status |
| `POST /chat/session` | create a conversation session |
| `GET /chat/session/{id}` | the session's message history + lifecycle state |
| `DELETE /chat/session/{id}` | close a session (terminal) |
| `GET /chat/health` | aggregate readiness of the conversation components |
| `GET /chat/version` | the conversation-stack versions |

Thin transport: validates → orchestrates Engine → Retrieval → Prompt → LLM → serialises. Error
taxonomy `400/404/409/429/503/500` (LLM categories mapped, never leaked). Owns the `/chat/*`
sub-namespace; the legacy exact `POST /chat` is untouched.

## Guarantees
- **Read-only + explanation-only:** never predicts / retrains / recalculates confidence / advises /
  modifies data; imports neither engine (AST); talks only to Decision Intelligence.
- **Deterministic + reproducible:** every module stamps a version and (where applicable) a SHA-256
  checksum; identical inputs → identical intent / retrieval / prompt / response.
- **Honest:** absent information is reported as `INSUFFICIENT_DATA` / `NOT_AVAILABLE` /
  `NOT_SUPPORTED`; missing citations are rejected; no fabrication.
- **Traceable:** citations flow from retrieval → prompt → response.

## Versions
`cnv-1` (domain) · `int-1` (intent) · `ret-1` (retrieval) · `prm-1` (prompt) · `eng-1` (engine) ·
`llm-1` (LLM adapter) · API `1`. Release `v0.6.0`, tag `v0.6.0-conversation-intelligence`.

## Storage
**None** — in-memory sessions only; the layer writes nothing and adds no migration.

## Honest note
Consistent with `docs/RESULTS.md`: this layer **manufactures no edge and gives no advice**. It
explains existing evidence; on today's near-empty corpus it honestly reports `INSUFFICIENT_DATA`, and
without a configured LLM provider the `EchoProvider` returns a clearly-marked stub. The only verified
edge remains the Outcome Engine (backtest-only).
