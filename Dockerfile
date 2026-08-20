# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# OS deps (psycopg2, build tools, healthcheck curl, Chromium deps for Playwright)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev curl postgresql-client \
    # Playwright Chromium dependencies
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
    libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation fonts-noto-color-emoji fonts-unifont && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first for better caching
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser (for PDF generation)
RUN playwright install chromium

# Copy project
COPY . .

# Default runtime env (can be overridden by platform)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=false

EXPOSE 8000

# Simple healthcheck on API status endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/api || exit 1

CMD ["python", "-u", "start.py"]
