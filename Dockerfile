FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nautilus/pyproject.toml nautilus/uv.lock ./nautilus/

RUN uv sync --frozen --no-dev
RUN cd nautilus && uv sync --frozen --no-dev

COPY . .

ENV PYTHONUNBUFFERED=1

WORKDIR /app/nautilus
CMD ["uv", "run", "nautilus-papertrade", "--instrument", "btcusdt_perp_binance", "--strategy", "volume_spike_trend", "--data-source", "ccxt"]
