# Pinned: the base image by digest, libraries from hashed lock files (tools/lock.sh)
FROM python:3.15.1-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

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
