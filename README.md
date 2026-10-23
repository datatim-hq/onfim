# Onfim

**English** · [Русский](README.ru.md)

**Long-term memory for AI agents that can explain what it remembers and why.**

Onfim is a self-hosted memory service for agents (Claude Code, Claude Desktop, Cursor, your own bots). It runs on
Postgres + pgvector, computes embeddings locally and exposes the same operations over REST and MCP.

> **Status: pre-release (0.1 in preparation).** Packaging, documentation and the quick start are being finished.

## What makes it different from a vector store

- **Evidence for every fact.** Each record carries its sources: who said it, when, and a short quote.
- **Strength, not just similarity.** Knowledge gains strength from independent confirmations and use, and decays with
  a per-kind half-life. Ranking combines relevance and strength by a documented formula.
- **Slots and supersession.** A newer claim replaces an older one only when it is confirmed (gates G1–G3);
  disputed changes go to a human review queue, with history and rollback.
- **Sleep.** Light sleep (nightly, deterministic) and deep sleep (weekly, optionally with an LLM that only *proposes*;
  every proposal passes deterministic checks) clean up, merge duplicates and report honestly.
- **The service never rewrites what the client sent.** No LLM on the write path.
- **Access model from day one.** Organizations, people, nested groups, memory layers (personal → team → org),
  grants, classification levels, row-level security in Postgres. An agent never sees more than its person.
- **Poisoning defense.** Rules from untrusted sources wait for confirmation, hidden Unicode is stripped,
  secrets are refused, related quotes do not count as independent confirmations.

## Repository layout

| Path | What |
|---|---|
| `mem/` | the service (Python package `mem`): `service.py` holds the business logic, `api.py` and `mcp_server.py` are thin adapters |
| `migrations/` | SQL schema; the service refuses to start if schema, embedding model or dimension do not match |
| `tests/` | unit and integration tests; registry of what each test checks — `docs/tests.md` |
| `docs/design/` | design specs 00–09 (Russian originals in `docs/ru/design/`); the core is `05-lifecycle.md` |
| `tools/` | embedding benchmark, the test registry builder and the quick start check run in CI |

## Running the tests

```bash
docker compose -f docker-compose.test.yml run --rm --build tests
```

Integration tests use a throwaway Postgres in tmpfs and a deterministic fake embedder.

## Running the service

```bash
cp .env.example .env && sed -i "s/change-me/$(openssl rand -hex 16)/" .env
docker compose up -d                                   # API and MCP on 127.0.0.1:3120
docker compose exec api python -m mem.cli init --tenant acme --person alice --name "Alice"
docker compose restart api                             # MCP picks up the new namespace
```

`init` prints a token once and the MCP address (`/mcp/alice.work/`). Put the service behind a TLS reverse proxy
before exposing it ([operations](docs/operations.md#running-behind-a-tls-reverse-proxy)), then connect Claude Code,
Claude Desktop or Cursor as described in [connecting clients](docs/connect-clients.md).

## Documentation

- [Concepts](docs/concepts.md) — records, evidence, strength, slots and supersession, sleep, trust and the access model
- [REST API](docs/rest-api.md) — every route with rights, fields and examples
- [MCP](docs/mcp.md) — the endpoint and its 16 tools
- [Connecting clients](docs/connect-clients.md) — Claude Code, Claude Desktop, Cursor
- [Operations](docs/operations.md) — configuration, CLI, reverse proxy, sleep worker, alerts, backups, upgrades
- [Design specs](docs/design/README.md) — requirements, architecture, data model, search, life cycle, verification,
  access model and team memory in full
- [Choosing the embedding model](docs/research/embed-benchmark.md) — the measurements behind the default model

The user documentation is also available [in Russian](README.ru.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
