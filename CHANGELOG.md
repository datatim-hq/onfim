# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - not released yet

First public release.

### Added

- Memory service over REST and MCP with the same operations: remember, recall, recall across layers, get, list,
  history, add evidence, used/helpful signals, confirm, pin, forget, review queue, sleep reports, stats.
- Evidence on every record; strength from independent confirmations and use, decaying by a per-kind half-life.
- Slots and supersession with confirmation gates and a human review queue.
- Light sleep (nightly, deterministic) and deep sleep (weekly; an optional LLM only proposes changes).
- Access model: organizations, people, agents acting on behalf of people, nested groups, personal, team and
  organization layers, grants, classification levels, row-level security in Postgres.
- Poisoning defense: untrusted sources, hidden Unicode stripping, secret detection.
- `mem init` for the first organization, person, namespace and agent token.
- `mem.cli check-client` to verify what a token can do in a namespace. CLI errors go to stderr with exit code 1;
  reusing a principal or namespace ID of another organization or owner is an error, not a silent update.
- Alerts to a webhook or Telegram, optionally through a SOCKS5 proxy (`MEM_TG_PROXY`).
- MCP tool descriptions in English, Russian with `MEM_MCP_LANG=ru`.
- Error messages, alerts, review questions and deep-sleep prompts in English; code comments and docstrings in
  English, checked in CI.
- Documentation in English and Russian (`README.ru.md`, `docs/ru/`), design specs included; CI checks that every
  page has its translation and that links resolve.
- Bulk `forget` by `source_ref`: everything taken from one source fragment at once. Purging a record also deletes the
  review queue items that quote it.
- Deep-sleep models are explicit: no default provider; a stage without a configured model is `DISABLED`.
- Docker Compose setup, container image on GHCR for each release tag. Base images pinned by digest, libraries
  installed from hashed lock files.

### Security

- Dependencies at fixed versions: FastAPI 0.161 with Starlette 1.9, MCP SDK 1.30, certifi 2026.10.19, pytest 9.0.3
  (tests only).
- Known and not applicable: Pillow 12.3 has open advisories in image parsing. It comes with fastembed, which caps it
  below 13; Onfim embeds text only and never opens images.
