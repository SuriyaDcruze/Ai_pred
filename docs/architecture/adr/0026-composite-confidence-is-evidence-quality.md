# ADR 0026 — Composite confidence is an evidence-quality indicator (not a prediction)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
The word "confidence" is the most dangerous word in this project. A "composite confidence" over a
decision object could easily be **misread as a probability of success** — a new trading signal — which
is exactly the prescriptive edge claim the project has disproven (`docs/RESULTS.md`: the only verified
edge is the Outcome Engine, backtest-only). The Milestone-4 confidence must measure something real
and useful **without** becoming a prediction.

## Decision
Composite Confidence answers **only** *"how trustworthy is the assembled evidence?"* — an
**evidence-quality** indicator. It is derived deterministically from evidence **completeness,
consistency, provenance, sample breadth, and conflicts**, reusing existing outputs; it computes no
new statistic. It is explicitly **NOT** a probability of success, a prediction/market/trading/AI
confidence, or a buy/sell/hold signal. The decisive property: **a high-confidence prediction with no
historical/similar/learning support scores LOW composite confidence** (thin evidence ⇒ low
trustworthiness of the *assembled picture*), even though the model's own stored confidence is high —
asserted by a test. **Conflicts** (outcome-model disagreement, similar-cases disagreement, incomplete
provenance, version mismatch) are **recorded, never hidden**, and each lowers the score. **Prioritisation**
organises objects by **evidence strength only** — never prediction outcome or future information, and
never implies an action.

## Alternatives considered
- *Use the stored prediction/outcome probability as the composite confidence* — rejected: that IS a
  prediction confidence; it says nothing about whether the *assembled evidence* is trustworthy, and
  it would read as a trading signal.
- *Hide conflicts to present a cleaner score* — rejected: conflicts are the most honest signal;
  they are recorded and explained.
- *Let prioritisation use realised outcomes* — rejected: that is future/outcome information; only
  evidence structure is used.

## Consequences
- **Positive:** a genuinely useful, honest quality signal that can never be mistaken for a trade
  recommendation; every factor/penalty/strength traces to a subsystem output.
- **Negative / accepted:** on a thin corpus most objects score LOW/INSUFFICIENT — the honest,
  intended result.
- **Enforced by:** the evidence-quality-≠-prediction-confidence test; deterministic aggregation +
  conflict tests; the no-trading-signal language test; validation of score/factor/reference ranges.
