# Pinned: the base image by digest, libraries from hashed lock files (tools/lock.sh)
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

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
