# Pinned: the base image by digest, libraries from hashed lock files (tools/lock.sh)
FROM python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/models HF_HOME=/models/hf

WORKDIR /app
COPY requirements.lock requirements-dev.lock ./
ARG DEV=0
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
 && if [ "$DEV" = "1" ]; then pip install --no-cache-dir --require-hashes -r requirements-dev.lock; fi

COPY mem ./mem
COPY migrations ./migrations
COPY tests ./tests
COPY tools ./tools

RUN useradd -r -u 10001 mem && mkdir -p /models /exports && chown mem /models /exports
USER mem

EXPOSE 3120
CMD ["python", "-m", "mem.main"]
