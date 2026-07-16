# Volume 22 — Frontend Architecture

## Purpose
Define the client: how the dashboard is structured, its state, theming, and the path from
a single-file page to a componentised app.

## Status: 🟡 Single-file — `app/dashboard/static/index.html` (~400 lines, glacier UI)

## Current
- One self-contained HTML file: glassmorphism "glacier" theme, mobile-first responsive,
  `lightweight-charts` for the chart, vanilla JS fetching the REST/WS endpoints.
- Cards: live chart, the verdict (direction + outcome combined), Deep Analysis, Sector,
  My Rules, News, NSE screener, chat.
- Zero build step — ships as a static file served by FastAPI. Fast to iterate.

## Principles (keep)
- **Honest UI:** WAIT is a first-class state; the learning-mode banner + disclaimers are
  always visible; every number shown comes from an engine response.
- **Mobile-first:** single column on phones → 2-col ≥860px; touch-friendly controls.
- **Theme-aware, self-contained** glacier aesthetic.

## When to componentise (target)
The single file is correct *now* (one dev, fast iteration). Migrate to a component
framework (Vite + React/Svelte) when: multiple views/routes (portfolio, track record,
profile), shared state grows, or a team forms. The **stable JSON API** (Vol 20) makes this
a clean swap — the backend doesn't change.

## Target structure (when migrated)
```
src/
  api/        typed clients for Aegis endpoints
  components/ Chart, VerdictCard, DeepAnalysis, Screener, Chat, TrackRecord
  views/      Dashboard, Portfolio, TrackRecord, Profile
  state/      lightweight store (user, current symbol, prefs)
  theme/      glacier tokens (light/dark)
```

## Failure handling
- Every fetch degrades gracefully ("could not load"); the chart survives WS drop; a failed
  panel never blanks the page.

## Testing
- Now: JS syntax check in CI (`new Function`), manual e2e. Target: component tests +
  Playwright e2e of the key flows.

## Prediction-Model / LLM integration
- The UI renders engine outputs and the assistant's explanations; it computes nothing
  market-related itself.

## Future
- Componentised app; PWA (Vol 23); portfolio & track-record views; richer explainability
  visualisations (similar-setup charts, sector heatmap).
