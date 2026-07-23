# Architecture Decision Records (ADR)

An ADR captures **one** significant architectural decision: its context, the decision, and
the consequences we accept. ADRs are immutable once accepted — a later decision that
changes course is a *new* ADR that supersedes the old one (never an edit).

Format: [Michael Nygard's template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Index

| ADR | Title | Status | Sprint |
|---|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith (not microservices) | Accepted | 1 |
| [0002](0002-prediction-engine-immutable.md) | The Prediction Engine is immutable | Accepted | 1 |
| [0003](0003-outcome-engine-immutable.md) | The Outcome Engine is immutable | Accepted | 1 |
| [0004](0004-forward-testing-before-production.md) | Forward testing before production / real money | Accepted | 1 |
| [0005](0005-single-prediction-history-db.md) | A single `prediction_history.db` | Accepted | 1 |
| [0006](0006-rest-api-separation-from-engine-logic.md) | REST API separated from engine logic | Accepted | 1 |

Related: the full Architecture Book lives in [../](../) (Volumes 00–28) and the
Architecture Review in [../ARCHITECTURE-REVIEW-v1.md](../ARCHITECTURE-REVIEW-v1.md).
