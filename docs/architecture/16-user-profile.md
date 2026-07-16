# Volume 16 — User Profile

## Purpose
Represent **who the user is** — identity, preferences, watchlist, risk appetite, and
history — so Aegis can personalise analysis and remember context across sessions.

## Status: 🔴 Not built (single-user today) — specified here. Prerequisite for multi-user.

## Responsibilities
- Identity & auth linkage (Vol 24).
- Preferences: default market/timeframe, risk appetite (→ Risk & Portfolio engines),
  favourite sectors, notification settings.
- **Watchlist:** the stocks a user tracks (drives screener scope, alerts, memory).
- Per-user **history** link (Historical Memory, Vol 13) — their own track record.

## Inputs / Outputs
- **In:** authenticated user id; profile updates.
- **Out:** `UserProfile { id, prefs, watchlist, risk_appetite, created_at }`; consumed by
  Portfolio, Risk, Conversation, Notification engines.

## Architecture (target)
```
Auth (Vol 24) → user id
  → Profile store (Postgres) ← preferences, watchlist, risk appetite
  → engines read profile for personalisation (risk %, default scope, sectors)
```

## Data (Vol 21)
- `users`, `user_preferences`, `watchlists`, joined to the prediction/trade records.

## API integration (target)
- `GET/PUT /profile`, `GET/PUT /watchlist`. All behind auth.

## Security / privacy
- PII minimisation; profiles are per-user isolated; no market credentials stored (Aegis
  places no orders). GDPR/India-DPDP-aware retention & deletion.

## Failure / logging
- Missing profile → sensible defaults (current single-user behaviour), never a hard fail.

## Testing (target)
- Profile CRUD, watchlist isolation between users, preference propagation to Risk/Portfolio.

## Prediction-Model integration
- Personalises *scope and risk*, never the model. Two users see the same model outputs;
  they differ in which stocks, sizing, and framing.

## LLM integration
- The assistant recalls preferences ("you usually swing-trade banking; here's today's
  banking setup") — from the profile, not invented.

## Future
- Roles (retail/analyst/admin), teams, saved analyses, personalised learning summaries.
