# Pinned: the base image by digest, libraries from hashed lock files (tools/lock.sh)
FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

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
