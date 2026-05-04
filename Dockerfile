FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "papertrade", "--instrument", "btcusdt_perp_binance", "--strategy", "volume_spike_trend", "--data-source", "ccxt"]
