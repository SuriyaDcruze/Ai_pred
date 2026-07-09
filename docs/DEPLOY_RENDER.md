# Deploy Aegis to Render

## TL;DR
Yes, it runs on Render. It's a FastAPI + WebSocket app. Follow the steps below.

---

## Steps

### 1. Put the code on GitHub
Render deploys from a Git repo.
```bash
git init
git add .
git add -f artifacts/model_best.pt   # IMPORTANT: .pt is gitignored — force-add your model
git commit -m "Aegis trading AI"
git branch -M main
git remote add origin https://github.com/<you>/aegis.git
git push -u origin main
```
> The trained model (`artifacts/model_best.pt`, ~2.4 MB) **must** be in the repo, or
> the app falls back to the low-confidence heuristic. `git add -f` overrides `.gitignore`.

### 2. Create the service on Render
- Render → **New → Blueprint** → pick your repo. It reads `render.yaml` automatically.
- Or **New → Web Service** manually with:
  - Build: `pip install -r requirements.txt`
  - Start: `uvicorn app.api.main:app --host 0.0.0.0 --port $PORT`
  - Health check path: `/health`

### 3. Open it
- `https://<your-service>.onrender.com/dashboard/`
- API docs: `https://<your-service>.onrender.com/docs`

---

## The gotchas (read these — they'll bite you otherwise)

| Issue | What happens | Fix |
|-------|--------------|-----|
| **RAM** | Torch + model needs ~600 MB–1 GB. Render **free (512 MB) will OOM-restart.** | Use **Standard (2 GB)** — `render.yaml` sets `plan: standard`. |
| **Ephemeral disk** | Without a disk, `data/calls.json` (your track record) resets on every restart/redeploy. | `render.yaml` mounts a 1 GB persistent disk at `data/`. (Disks need a paid plan.) |
| **Binance geo-block** | `api.binance.com` returns HTTP 451 from cloud IPs. | Already handled — the app auto-falls back to `data-api.binance.vision`. ✅ |
| **Cold starts (free)** | Free services sleep after 15 min idle; first request is slow. | Standard plan stays warm. |
| **Chat LLM** | GPT-style chat needs a key. | Set `ANTHROPIC_API_KEY` in Render env vars (optional; works without it). |
| **Build time** | Torch CPU wheel is big (~200 MB); first build takes a few minutes. | Normal — subsequent builds cache. |

---

## Cost reality
- **Free tier**: will likely crash on torch (512 MB). Fine only for a quick test, no disk.
- **Standard (~$25/mo, 2 GB)**: comfortable — this is what `render.yaml` targets.
- Training is **not** done on Render — train on Colab (GPU), download `model_best.pt`, commit it, redeploy.

## Honest note
Hosting makes the platform reachable from anywhere — but it's still the ~51.6% model.
Deploying doesn't change the edge; it just puts the same tool on the web.
