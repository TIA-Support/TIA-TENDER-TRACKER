# ──────────────────────────────────────────────────────────────────────────────
#  TIA Solutions — ICT Tender Tracker
#  Docker image
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install Playwright's Chromium system dependencies in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages first (better layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + all its OS-level dependencies
RUN playwright install --with-deps chromium

# Copy application source
COPY app.py        .
COPY crawler/      ./crawler/
COPY templates/    ./templates/
COPY static/       ./static/

# Persistent data volume mount-point (tenders.db lives here)
RUN mkdir -p /data

EXPOSE 5000

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
