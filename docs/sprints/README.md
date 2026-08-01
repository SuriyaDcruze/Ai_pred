# Sprint index

AEGIS ships **sprint by sprint** under a strict gated process: **plan → approve → implement one
milestone → review → freeze**. Each sprint is additive and frozen at a tagged release; none touches
the immutable Prediction/Outcome engines. This index links every sprint's plan, report, as-built
volume (where one exists), and release notes.

| Sprint | Engine | Version · tag | Plan | Report | As-built | Release |
|---|---|---|---|---|---|---|
| 1 | Forward Testing | `0.1.0` · `v0.1.0-forward-testing` | [plan](../architecture/sprints/sprint-01-forward-testing-plan.md) | [report](sprint-01-report.md) | Vol 18 | [notes](../releases/v0.1.0-forward-testing.md) |
| 2 | Historical Memory | `0.2.0` · `v0.2.0-historical-memory` | [plan](../architecture/sprints/sprint-02-historical-memory-plan.md) | [report](sprint-02-report.md) | Vol 13 | [notes](../releases/v0.2.0-historical-memory.md) |
| 3 | Similarity Engine | `0.3.0` · `v0.3.0-similarity-engine` | [plan](../architecture/sprints/sprint-03-similarity-plan.md) | [report](sprint-03-report.md) | Vol 14 | [notes](../releases/v0.3.0-similarity-engine.md) |
| 4 | Learning Engine | `0.4.0` · `v0.4.0-learning-engine` | [plan](../architecture/sprints/sprint-04-learning-plan.md) | [report](sprint-04-report.md) | Vol 15 | [notes](../releases/v0.4.0-learning-engine.md) |
| 5 | Decision Intelligence | `0.5.0` · `v0.5.0-decision-intelligence` | [plan](../architecture/sprints/sprint-05-decision-intelligence-plan.md) | [report](sprint-05-report.md) | [as-built](../architecture/decision-intelligence-engine.md) | [notes](../releases/v0.5.0-decision-intelligence.md) |
| 6 | Conversation Intelligence | `0.6.0` · `v0.6.0-conversation-intelligence` | [plan](../architecture/sprints/sprint-06-conversation-plan.md) | [report](sprint-06-report.md) | [as-built](../architecture/conversation-intelligence-engine.md) | [notes](../releases/v0.6.0-conversation-intelligence.md) |
| 7 | Agent Engine | `0.7.0` · `v0.7.0-agent-engine` | [plan](../architecture/sprints/sprint-07-agent-plan.md) | [report](sprint-07-report.md) | [as-built](../architecture/agent-engine.md) | [notes](../releases/v0.7.0-agent-engine.md) |

**Decisions:** every sprint's architectural decisions are recorded as immutable ADRs in
[../architecture/adr/](../architecture/adr/) (Sprint 1 → 0001–0006 … Sprint 7 → 0035–0041).
**Honest scoreboard:** [../RESULTS.md](../RESULTS.md) — the only verified edge remains the Outcome
Engine (backtest-only); every layer since is read-only orchestration/explanation that adds no edge.
**Changelog:** [../../CHANGELOG.md](../../CHANGELOG.md).
