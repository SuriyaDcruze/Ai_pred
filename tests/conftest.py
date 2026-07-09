import pandas as pd
import pytest

from app.data.synthetic import generate_ohlcv


@pytest.fixture(scope="session")
def ohlcv() -> pd.DataFrame:
    return generate_ohlcv(n=600, seed=1)
