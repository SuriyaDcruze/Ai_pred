"""Integration smoke tests: a tiny training run + the HTTP API surface."""

import argparse
import os

import torch
from fastapi.testclient import TestClient

from app.ai.predictor import Predictor
from app.training.train import train


def test_training_smoke_and_checkpoint(tmp_path):
    out = tmp_path / "model.pt"
    args = argparse.Namespace(
        symbol="X", interval="1h", futures=False, synthetic=True, bars=700,
        seq_len=32, horizon=3, epochs=1, folds=2, batch_size=32, lr=3e-4,
        patience=3, seed=1, out=str(out), run_name="test",
    )
    path = train(args)
    assert os.path.exists(path)

    # The checkpoint must reload into a working predictor.
    pred = Predictor.load(path=path, device="cpu")
    from app.data.synthetic import generate_ohlcv

    df = generate_ohlcv(n=200)
    p = pred.predict(df)
    assert abs((p.p_bullish + p.p_bearish + p.p_sideways) - 1.0) < 1e-4


def test_health_endpoint():
    from app.api.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "device" in body


def test_analyze_endpoint_with_supplied_candles():
    from app.api.main import app
    from app.data.synthetic import generate_ohlcv

    df = generate_ohlcv(n=200)
    candles = [
        {
            "open_time": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low,
            "close": row.close, "volume": row.volume, "trades": int(row.trades),
        }
        for ts, row in df.iterrows()
    ]
    with TestClient(app) as client:
        resp = client.post(
            "/analyze",
            json={"symbol": "BTCUSDT", "exchange": "synthetic", "timeframe": "1h", "candles": candles},
        )
        assert resp.status_code == 200
        assert "signal" in resp.json()
