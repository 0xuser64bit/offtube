# offtube — small runtime image (python + ffmpeg + deno + yt-dlp).
# Deno is yt-dlp's JS runtime for PO-token/JS challenges (node would need
# >= 23.5; Debian bookworm only ships node 18, which yt-dlp rejects as
# unsupported). ffmpeg is required for merge/trim/audio-extract.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# ffmpeg for merge/trim; deno (single static binary) for yt-dlp challenges.
# curl/unzip are build-only and purged in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && cp /root/.deno/bin/deno /usr/local/bin/deno \
    && deno --version \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.deno /root/.cache

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app.py ./
COPY web/ ./web/
RUN mkdir -p downloads cookies \
    && touch cookies/.gitkeep downloads/.gitkeep

VOLUME ["/app/downloads", "/app/cookies"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["python", "app.py"]
