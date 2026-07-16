# Volume 23 — Mobile Architecture

## Purpose
Define how Aegis reaches mobile — where most Groww users actually are.

## Status: 🔴 Responsive web only (no native/PWA yet).

## Current
- The dashboard is **mobile-first responsive** (single-column, touch-friendly, viewport
  meta, 42–46px controls). Works in a mobile browser today.

## Strategy (staged, honest)
1. **Now — responsive web.** Correct for the stage. Zero extra cost; one codebase.
2. **Next — PWA.** Add a manifest + service worker to the (componentised) frontend
   (Vol 22): installable, offline shell, push notifications (Vol 27). ~90% of "app" value
   at ~10% of native cost.
3. **Later — native (if justified).** React Native / Flutter *only* if PWA limits bite
   (deep OS integration, app-store distribution demand). Not before product-market fit.

> Do not build a native app before there's a live track record and real demand — same
> over-engineering discipline as microservices (Vol 04).

## API integration
- Mobile consumes the same versioned JSON API (Vol 20) — no separate backend. Auth via
  the same tokens (Vol 24).

## UX priorities for mobile
- The **verdict** and **Deep Analysis** first (glanceable BUY/SELL/WAIT + why).
- Screener as a scannable list; chat as the primary interaction.
- Notifications: "a TAKE setup appeared on your watchlist" (Vol 27).

## Testing
- Responsive/device testing now; PWA install + offline + push when built.

## Future
- PWA → optional native shell; widget ("today's setups"); watch-list push alerts.
