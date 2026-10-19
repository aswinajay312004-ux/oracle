# POPPYS AI application image (docs/DOCKER.md). Oracle is NOT in this image: it stays in its own container
# (oracle26ai on the owner's laptop), which holds the GARMENT_DEMO data. Passwords are never baked in: .env.poppys is
# mounted read-only at run time.
FROM python:3.13-slim

# tzdata: the OC number's financial year and every time shown come from the local clock (TZ in docker-compose.yml).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY config ./config
COPY scripts ./scripts

# A non-root account owns only the data folder (SQLite store, uploaded originals, logs).
RUN useradd --system --uid 10001 --home-dir /app poppys \
 && mkdir -p /app/data \
 && chown poppys:poppys /app/data
USER poppys

EXPOSE 8008
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8008/healthz', timeout=4).status == 200 else 1)"

# 0.0.0.0 inside the container only; docker-compose.yml publishes the port on 127.0.0.1 of the laptop, never the network.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8008"]
