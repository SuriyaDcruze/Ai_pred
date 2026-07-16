# Volume 02 — Product Requirements

## 1. User stories (primary — NSE / Groww)

| # | As a… | I want to… | So that… | Status |
|---|-------|-----------|----------|--------|
| U1 | swing trader | ask "should I buy Reliance today?" | get a clear, explained BUY/SELL/WAIT | 🟢 `/intelligence` |
| U2 | investor | see which NSE stocks have a setup today | I don't scan 50 charts | 🟢 `/screener/nse` |
| U3 | trader | know the entry, stop, and target | I can place it on Groww | 🟢 screener/intel plan |
| U4 | user | understand *why* it says WAIT | I trust the tool | 🟢 For/Against factors |
| U5 | investor | know the sector's strength | I avoid weak-sector longs | 🟢 `/sectors` |
| U6 | user | see similar historical setups & their win rate | I gauge the odds | 🟢 similarity engine |
| U7 | investor | give ₹X and get an allocation | I diversify with sizing | 🔴 Portfolio (Vol 11) |
| U8 | user | have the AI log its calls and score them live | I see real proof over time | 🔴 Forward Testing (Vol 18) |
| U9 | user | chat naturally and get analyst-grade answers | it feels like an expert | 🟡 Conversation (Vol 07) |
| U10 | user | learn risk management / TA concepts | I improve as a trader | 🟡 chat + patterns |
| U11 | user | compare TCS vs Infosys | I pick the better setup | 🔴 needs compare flow |
| U12 | returning user | know "what changed since yesterday" | I stay current | 🔴 needs memory + diff |

## 2. Functional requirements (by capability)

- **FR-Prediction:** produce Direction, Probability, Confidence for a symbol/timeframe,
  from the calibrated model only. Calibrated so "60%" ≈ 60% real.
- **FR-Outcome:** produce P(target-before-stop) and a TAKE/VETO decision for a directional
  setup. Default to WAIT when not confident.
- **FR-Intelligence:** assemble market state, relative strength, sector, similarity, plan,
  and plain-English reasoning into one explainable report.
- **FR-Screener:** scan a basket of NSE stocks and rank TAKE setups with levels.
- **FR-Risk:** compute ATR stop, R-multiple targets, position size ≤ configured account
  risk; never recommend all-in; no martingale.
- **FR-Conversation:** interpret intent → call the right engines → explain results;
  never fabricate a prediction.
- **FR-Portfolio (future):** given capital, produce sized, diversified, correlation-aware
  allocation across current TAKE setups.
- **FR-ForwardTest (future):** log every recommendation with its levels; score WIN/LOSS
  against real future price; maintain a running live track record.

## 3. Non-functional requirements

| Category | Requirement |
|---|---|
| **Honesty** | Every user-facing number is real & reproducible; backtest-vs-live always labelled; disclaimers persistent. |
| **Research integrity** | No change ships without purged walk-forward + untouched-test evidence; leakage tests must pass. |
| **Performance** | Single-symbol analysis ≤ ~10s (model + data fetch); screener (15 stocks) ≤ ~40s; dashboard interactive. |
| **Reliability** | Data-source failure degrades gracefully (mirror/poll fallback); a broken engine never breaks the signal. |
| **Security** | No brokerage credentials required for read; secrets via env; (future) auth for multi-user. |
| **Compliance (SEBI)** | Decision-support/education, not registered advice; no order execution; audit trail of recommendations. |
| **Explainability** | Every recommendation carries reasons, factors, and what-invalidates. |
| **Testability** | Deterministic unit tests; leakage/future-invariance tests; e2e smoke of key endpoints. |
| **Observability** | Structured logs now; (future) metrics + drift detection. |
| **Maintainability** | Clean module boundaries; shared `FeatureBuilder`; no duplicate feature logic. |

## 4. Acceptance criteria (representative)

- A recommendation must **never** be shown without a recommendation reason and a
  disclaimer.
- The Prediction/Outcome outputs shown to the user must originate **only** from the
  models — verified by the LLM layer having no prediction capability (Vol 07).
- Any "improvement" claimed in docs must be backed by a report in `reports/` produced
  by the honest pipeline.
- WAIT is a valid, first-class output — the UI must present it as a decision, not an
  error or a blank.

## 5. Out of scope (now)

Order execution, real-money automation, intraday tick trading, options/derivatives,
multi-tenant billing, native mobile apps, and any "guaranteed returns" framing — all
explicitly excluded.
