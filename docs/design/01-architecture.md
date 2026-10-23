# 01. Architecture and deployment

**English** · [Русский](../ru/design/01-architecture.md)

## Components

```text
 Claude Code / Desktop ─MCP (streamable HTTP, Bearer)──┐
 assistant agent ───────REST (Bearer)──────────────────┤
                                                       ▼
                               nginx (mem.example.com, TLS)
                                                       │
                                  127.0.0.1:3120  ┌────┴──────────────┐
                                                  │  mem-api           │
                                                  │  FastAPI + MCP     │
                                                  │  (one process)     │
                                                  │  embedder (ONNX)   │
                                                  └────┬──────────────┘
                                                       │
                                            ┌──────────┴─────────┐
                                            │ Postgres 16        │
                                            │ + pgvector         │
                                            │ (separate,         │
                                            │  mem_db)           │
                                            └────────────────────┘
          mem-worker (same image, job mode): light sleep daily at 03:30, deep sleep on Sundays at 04:00 (MEM_TZ)
```

| Component | Technology | Why |
|---|---|---|
| API + MCP | Python 3.12, FastAPI, the official Python MCP SDK (streamable HTTP), one process, a shared service layer | P7: one code path for REST and MCP |
| Embeddings | An ONNX model via `fastembed` (no torch), inside the API process | P9 local; no separate service |
| Model | **`intfloat/multilingual-e5-small` (384)** — chosen by a benchmark on 24 Sept 2026 from three candidates (`docs/research/embed-benchmark.md`) | Quality on Russian checked on the canaries |
| Database | Postgres 16 + pgvector (HNSW), FTS with `russian` and `english` configurations | One engine, familiar operations |
| Migrations | SQL files `migrations/NNNN_*.sql`, an own runner with an advisory lock; at start the service checks the applied migrations, the vector dimension and model, and does not start on a mismatch (no ORM, so no Alembic) | P10 |
| Nightly job | the same image, `python -m mem.jobs.nightly`, cron in a scheduler container | A separate process that does not disturb the API |
| LLM (deep sleep only) | Claude through the Anthropic API: contradictions and slots — Sonnet, summaries — Haiku; answers by a JSON schema | F-8 |

## Deployment

| Parameter | Value |
|---|---|
| Start | `docker compose up -d`, Compose project `mem` (explicit `name:` in the Compose file) |
| Ports | API `127.0.0.1:3120`; Postgres not published; outside access through nginx with TLS |
| Backup | `pg_dump` on a schedule, encrypted, 14 days; plus a nightly JSONL export of every namespace |

### Resources

| Container | Limit |
|---|---|
| mem-api (with the model) | 1536 MB (about 820 MB at rest) |
| mem-worker | 512 MB (runs for the duration of a job) |
| mem-postgres | 512 MB |
| **Total** | **≈ 2.5 GB** |

### Schedule (default, time zone MEM_TZ)

| Time | Job |
|---|---|
| 03:30 | Light sleep, daily (05 §3) |
| 04:00, Sunday | Deep sleep (clean-up), after the light one; the report goes to the agent for the owner |
| 05:30 | Backup |

## Security

| ID | Requirement | Type |
|---|---|---|
| S-1 | Authentication: `Authorization: Bearer <token>`, tokens in the `clients` table (a hash is stored). A token belongs to a principal (08); its namespaces and rights are a **ceiling**, access itself comes from the principal's grant (for an agent, its person's) | DET |
| S-2 | Client tokens: `cowork` (work: read/write/review), `claude-code` (work: read/write), `assistant` (assistant: read/write/review/forget — `review` and `forget` are called only after the owner presses a button in the bot), `admin` (everything, localhost only) | DET |
| S-3 | `purge`, export and admin operations need the `forget`/`admin` right; bulk deletion needs a filter and `dry_run=false` + `confirm=<number of records>` (lesson: bulk deletion without confirmation is dangerous) | DET |
| S-4 | nginx: only `/mcp/` and `/v1/` are public, `/status` is on an unguessable path; scanners → 444 (there is a `default_server`) | DET |
| S-5 | Record texts are not logged; on purge `audit_log` gets only the id, namespace and rule | DET |
| S-6 | Embeddings and search are local. Only the deep sleep sends anything to an external LLM: record texts and quotes of one `subject` at a time, only to the Anthropic API (the same processor that already sees the agent's messages); records with `visibility = owner_only` are never sent | DET |

## Acceptance criteria

- `ss -tlnp`: only `127.0.0.1:3120` for mem.
- A request without a token → 401; the `cowork` token on the `assistant` namespace → 403.
- A bulk `forget` without `confirm` → 400, nothing deleted.
- `docker compose ls` shows the project `mem` and volumes `mem_*`.
