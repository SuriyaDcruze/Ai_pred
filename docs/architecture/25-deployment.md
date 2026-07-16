# Volume 25 — Deployment

## Purpose
Define how Aegis is built, configured, deployed, and how model artifacts are managed.

## Status: 🟡 Basic — `render.yaml`, `Dockerfile`, `requirements.txt`, Colab notebooks.

## Current
- **Render** web service (`render.yaml`) and **Hugging Face Spaces** Docker (port 7860)
  supported; CPU-only torch in `requirements.txt`. `docs/DEPLOY_RENDER.md` walks it.
- Model artifacts (`sklearn_model.pkl`, `outcome_model*.pkl`) committed (small) so a fresh
  clone runs. Colab notebooks for optional GPU training (legacy — logistic needs no GPU).

## Environments (target)
| Env | Purpose |
|---|---|
| dev | local (`uvicorn ... --reload`) |
| staging | pre-prod validation, forward-testing dry run |
| prod | the live platform |

## Config & secrets
- `pydantic-settings` env config; secrets (LLM key, DB URL) via env / secret manager —
  never in the image or repo.

## CI/CD (target)
```
push → CI: lint + full test suite (incl. leakage/future-invariance) + JS syntax
     → build image
     → deploy staging → smoke tests (/health, /intelligence)
     → manual gate → prod
```
- **Model artifact management:** artifacts versioned & stamped (Vol 21); promotion via the
  champion/challenger gate (Vol 15) — never hand-swap a model into prod without the gate.

## Scaling
- Vertical first (monolith). Horizontal: stateless API replicas behind a load balancer;
  the CPU-heavy screener/intelligence can move to workers (Vol 04 §7) if needed.

## Failure handling / rollback
- Immutable image tags; keep the previous model artifact (nightly retrain backs up the
  champion) for instant rollback.

## Observability hook
- Deploys emit version + model-version; health/readiness probes (Vol 27).

## Future
- IaC (Terraform), blue/green deploys, a scheduled worker for forward-testing & retrain,
  Postgres managed instance, Redis cache.
