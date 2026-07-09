"""SQLAlchemy 2.0 ORM models mirroring ``schema.sql``.

Persistence is optional (install extras: ``pip install -e '.[db]'``). The API
runs fully in-memory without a database; wire these in when you want durable
storage of candles, signals, and predictions.
"""

from __future__ import annotations

from datetime import datetime

try:
    from sqlalchemy import BigInteger, Float, Integer, String, UniqueConstraint
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

    class Base(DeclarativeBase):
        pass

    class CandleRow(Base):
        __tablename__ = "candles"
        __table_args__ = (
            UniqueConstraint("exchange", "symbol", "timeframe", "open_time"),
        )
        id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
        exchange: Mapped[str] = mapped_column(String)
        symbol: Mapped[str] = mapped_column(String)
        timeframe: Mapped[str] = mapped_column(String)
        open_time: Mapped[datetime]
        open: Mapped[float] = mapped_column(Float)
        high: Mapped[float] = mapped_column(Float)
        low: Mapped[float] = mapped_column(Float)
        close: Mapped[float] = mapped_column(Float)
        volume: Mapped[float] = mapped_column(Float)
        trades: Mapped[int] = mapped_column(Integer, default=0)

    class SignalRow(Base):
        __tablename__ = "signals"
        id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
        generated_at: Mapped[datetime]
        exchange: Mapped[str] = mapped_column(String)
        symbol: Mapped[str] = mapped_column(String)
        timeframe: Mapped[str] = mapped_column(String)
        decision: Mapped[str] = mapped_column(String)
        market_status: Mapped[str] = mapped_column(String)
        confidence: Mapped[float] = mapped_column(Float)
        payload: Mapped[dict] = mapped_column(JSONB)

    _SQLALCHEMY_AVAILABLE = True
except ImportError:  # pragma: no cover - db extra not installed
    _SQLALCHEMY_AVAILABLE = False
