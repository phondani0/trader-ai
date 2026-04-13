FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source code
COPY . .

# Create data and log directories
RUN mkdir -p data logs

# Environment
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/api/health').raise_for_status()" || exit 1

# Run the bot
CMD ["uv", "run", "python", "-m", "src.main"]
