-- Aegis persistence schema (PostgreSQL). Loaded on first container start.

CREATE TABLE IF NOT EXISTS candles (
    id           BIGSERIAL PRIMARY KEY,
    exchange     TEXT        NOT NULL,
    symbol       TEXT        NOT NULL,
    timeframe    TEXT        NOT NULL,
    open_time    TIMESTAMPTZ NOT NULL,
    open         DOUBLE PRECISION NOT NULL,
    high         DOUBLE PRECISION NOT NULL,
    low          DOUBLE PRECISION NOT NULL,
    close        DOUBLE PRECISION NOT NULL,
    volume       DOUBLE PRECISION NOT NULL,
    trades       INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (exchange, symbol, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (exchange, symbol, timeframe, open_time DESC);

CREATE TABLE IF NOT EXISTS signals (
    id                BIGSERIAL PRIMARY KEY,
    generated_at      TIMESTAMPTZ NOT NULL,
    exchange          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    decision          TEXT NOT NULL,
    market_status     TEXT NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL,
    entry_low         DOUBLE PRECISION,
    entry_high        DOUBLE PRECISION,
    stop_loss         DOUBLE PRECISION,
    take_profit_1     DOUBLE PRECISION,
    take_profit_2     DOUBLE PRECISION,
    risk_reward       DOUBLE PRECISION,
    position_size     DOUBLE PRECISION,
    payload           JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON signals (symbol, generated_at DESC);

CREATE TABLE IF NOT EXISTS predictions (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol        TEXT NOT NULL,
    p_bullish     DOUBLE PRECISION,
    p_bearish     DOUBLE PRECISION,
    p_sideways    DOUBLE PRECISION,
    predicted_close DOUBLE PRECISION,
    confidence    DOUBLE PRECISION
);
