# ADR 0031 — Provider-independent LLM adapter

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
The conversation layer must invoke an LLM, but the choice of provider (OpenAI, Azure OpenAI, a local
model) will change over time, and provider SDKs bring heavy dependencies, provider-specific exception
types, and non-deterministic response shapes. Coupling the conversation code to one SDK would make it
brittle, hard to test offline, and hard to swap.

## Decision
Introduce an **LLM Adapter** (`app/conversation/llm_adapter.py`) — a **provider-independent
infrastructure abstraction**. Consumers depend only on `LLMAdapter` / `LLMProvider` and the
normalised `LLMRequest`/`LLMResponse`/`LLMError` models; a new provider is a registered factory (no
consumer change). It performs **model communication only** (no retrieval / intent / prompt / session
logic). Providers are built against a **duck-typed client**, so **no LLM SDK is imported** here; the
default concrete provider is a deterministic **`EchoProvider`** (offline stub — no API key/network,
ideal for tests and no-LLM deployments), with `OpenAIProvider`/`azure_openai` designed for a thin SDK
translator. Provider faults are **normalised** into deterministic categories (`INVALID_REQUEST` /
`AUTHENTICATION_ERROR` / `RATE_LIMITED` / `PROVIDER_UNAVAILABLE` / `TIMEOUT` / `INTERNAL_ERROR`) and
**never leaked** as provider-specific exceptions.

## Alternatives considered
- *Call the OpenAI SDK directly in the conversation code* — rejected: SDK coupling, provider-specific
  errors, no offline/deterministic testing.
- *No stub provider* — rejected: the engine must be testable and deployable without an LLM; the
  `EchoProvider` provides deterministic behaviour offline.

## Consequences
- **Positive:** the LLM is fully replaceable and testable; errors are uniform; no SDK dependency
  ships; the stack runs offline with the stub.
- **Negative / accepted:** the stub returns a marked placeholder (not a real explanation) until a
  provider is configured; a real provider needs a small translator to the duck-typed client shape.
- **Enforced by:** adapter tests (abstraction, factory, validation, normalization, error mapping,
  health/version, determinism) + a no-SDK-import guard.
