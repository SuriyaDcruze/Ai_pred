# Volume 24 — Security Architecture

## Purpose
Protect users, data, and the platform's integrity — and encode the **compliance and
no-harm invariants** that make Aegis safe.

## Status: 🔴 Minimal (single-user, no auth) — specified here for productisation.

## The safety invariants (non-negotiable)
1. **No order execution.** No module places orders or holds brokerage trading credentials.
   This is the strongest user-protection guarantee — encoded in the architecture (Vol 04),
   not a setting.
2. **Prediction-Model independence.** The LLM cannot emit predictions (Vol 07) — prevents
   the model of "AI hallucinated a trade."
3. **Persistent disclaimers + audit trail.** Every recommendation carries "not SEBI advice;
   paper-trade first" and is logged immutably (Vol 13/21) — compliance & accountability.

## Target controls (multi-user)
- **AuthN/AuthZ:** JWT/session; per-user isolation; roles (retail/analyst/admin).
- **Secrets:** env-only (LLM keys, DB creds); never in repo; rotation policy.
- **Transport:** HTTPS everywhere; secure cookies; HSTS.
- **Input validation:** Pydantic on all inputs; strict symbol/timeframe allow-lists.
- **Rate limiting & abuse:** per-key limits; protect data-provider quotas & CPU.
- **PII:** minimise; encrypt at rest where required; DPDP/GDPR retention & deletion.

## LLM-specific security
- **Prompt injection:** market data, news headlines, and filings are **untrusted content**
  — treated as data, never instructions. Disclaimers appended server-side.
- **Tool safety:** the assistant's tools are read-only; no tool can trade, spend, or predict.
- **Output guarding:** never present LLM prose as a prediction or as advice.

## Data-source & supply chain
- No secrets needed for public market data (reduces attack surface). Pin dependencies;
  review model artifacts before shipping.

## Failure handling
- Fail closed on auth; fail safe (WAIT / "unavailable") on engine errors — never fabricate.

## Testing
- Auth tests, injection tests on the LLM layer, "no prediction tool" schema assertion,
  the no-order invariant as a documented, tested guarantee.

## Future
- SSO, 2FA, audit dashboards, SEBI-aligned record-keeping if the product monetises advice.
