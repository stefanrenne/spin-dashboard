# Spin Dashboard voor Unraid
# Debian trixie: fatrace >= 0.17 (filesystem-brede fanotify-marks, ook voor ZFS-datasets)
FROM python:3.13-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends fatrace hdparm smartmontools \
 && rm -rf /var/lib/apt/lists/*

COPY app/ /app/

ENV PORT=8089 \
    DATA_DIR=/data \
    EMHTTP_DIR=/emhttp \
    POLL_INTERVAL=60 \
    RETENTION_DAYS=30 \
    PYTHONUNBUFFERED=1

EXPOSE 8089
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/', timeout=4)" || exit 1

CMD ["python3", "/app/spindash.py"]
