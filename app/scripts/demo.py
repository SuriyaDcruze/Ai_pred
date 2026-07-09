"""End-to-end demo on synthetic data — no network, no GPU, no trained model.

    python -m app.scripts.demo
"""

from __future__ import annotations

from app.data.synthetic import generate_ohlcv
from app.service import AnalysisService
from app.utils.format import format_signal


def main() -> None:
    df = generate_ohlcv(n=800, seed=42)
    service = AnalysisService()  # heuristic predictor (no checkpoint required)
    signal = service.analyze(df, symbol="DEMOUSDT", exchange="synthetic", timeframe="1h")
    print(format_signal(signal))


if __name__ == "__main__":
    main()
