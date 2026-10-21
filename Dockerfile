# Spin Dashboard for Unraid
# Debian trixie: fatrace >= 0.17 (filesystem-wide fanotify marks, also for ZFS datasets)
FROM python:3.13-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends fatrace hdparm smartmontools \
 && rm -rf /var/lib/apt/lists/*

COPY app/ /app/

# set by CI: 1.2.3 for a release tag, main-<sha> for a build of main
ARG VERSION=dev
ENV SPINDASH_VERSION=${VERSION} \
    PORT=8089 \
    DATA_DIR=/data \
    EMHTTP_DIR=/emhttp \
    POLL_INTERVAL=60 \
    RETENTION_DAYS=30 \
    PYTHONUNBUFFERED=1

EXPOSE 8089
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4).read()" || exit 1

CMD ["python3", "/app/spindash.py"]
