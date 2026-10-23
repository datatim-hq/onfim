# 03. API: REST and MCP

**English** · [Русский](../ru/design/03-api.md)

## Purpose

One set of operations for all clients. REST is for the agent and scripts, MCP for Cowork and Claude Code. Both call one
service layer (P7): filters, rights and errors behave the same.

## Authorization

`Authorization: Bearer <token>` on every request except `/status/<secret>`. Token rights: namespaces × `read` /
`write` / `review` / `forget` / `admin` (01, S-1).

## REST (`/v1/{namespace}/…`)

### Writing and reading

| Method and path | Operation | Right |
|---|---|---|
| `POST /memories` | **remember**. Body: `text`, `kind`, `subject?`, `slot?`, `tags?`, `importance? (1–3)`, `visibility?`, `occurred_at?`, `external_ref?`, `supersedes?`, **`evidence`** (required, at least one: `source_ref`, `source_scope`, `speaker?`, `quote? (≤200)`, `observed_at`). Response: the record, its `state` (`current` / `candidate` / `superseded`) and, if the slot is taken, the id of the current record | write |
| `POST /memories/{id}/evidence` | add evidence to an existing record (reinforcement, 05 §1). A repeat with the same `source_ref` is ignored | write |
| `GET /memories/{id}` | the record + all evidence + the latest strength events | read |
| `GET /memories` | **list**: filters `kind`, `subject`, `slot`, `tags`, `tags_mode`, `state`, `created_after/before`, `as_of`, `audience`, `min_strength`, `limit`, `cursor`, sorting `strength` \| `created_at` | read |
| `POST /recall` | **recall**: `query`, the same filters, `include_candidates?`, `limit (≤50)`, `explain?` → records with strength, evidence and a dispute flag (04) | read |
| `GET /memories/{id}/history` | the chain of supersessions and candidates in the slot with `valid_from/valid_to` and the grounds (G1a/b/c, explicit, rollback) | read |

### Signals (reinforcement)

| Method and path | Event | Right |
|---|---|---|
| `POST /memories/{id}/used` | the record was used in an answer or a briefing | write |
| `POST /memories/{id}/helpful` | the record turned out helpful | write |
| `POST /memories/{id}/confirm` | owner confirmation (`owner_confirm`) — only tokens with the `review` right | review |
| `POST /memories/{id}/pin`, `/unpin` | forbid forgetting | review |
| `POST /memories/{id}/close` | for `commitment_mirror`: the external object is closed | write |

### Supersession, archive, deletion

| Method and path | Operation | Right |
|---|---|---|
| `POST /memories/{id}/revert-supersede` | undo a supersession within 30 days | review |
| `POST /memories/{id}/archive`, `/unarchive` | manual archive / restore | write |
| `DELETE /memories/{id}?mode=purge` | physically delete one record with all its evidence and events | forget |
| `POST /forget` | in bulk: a filter (`subject`/`slot`/`tags`/`kind`, at least one required) + `mode=archive\|purge` + `dry_run` (default `true`); does nothing without `dry_run=false` and `confirm=<N>` equal to the number of matching records | forget |

### Clean-up and the review queue

| Method and path | Operation | Right |
|---|---|---|
| `GET /sleep/latest?kind=light\|deep` | the report of the latest sleep (05, §3) with quotes | read |
| `GET /sleep/{job_id}` | the report of one run | read |
| `GET /review?status=open` | the queue: contradictions, "stale?", slot and summary proposals — with quotes | review |
| `POST /review/{id}/accept` \| `/reject` | a decision on an item; `accept` for a `contradiction` requires naming the right record (`winner`) | review |
| `POST /sleep/run` | run `light` or `deep` by hand, `dry_run` defaults to `true` | admin |

### Service routes

`GET /stats` (counts by kind and state, strength distribution), `GET /export`, `POST /import` (admin, `dry_run` by
default), `GET /status/<secret>`.

### Errors

`400` — invalid input (unknown `kind`, no `evidence`, `supersedes` pointing to another namespace or to a non-current
record), `401`, `403`, `404`, `409` — a slot conflict when `supersedes` names the wrong record explicitly, `503` — the
embedding model is unavailable (no record created, F-11). Body: `{"error": "<code>", "message": "<text>"}`.

## MCP

Endpoint: `https://mem.example.com/mcp/{namespace}/` (streamable HTTP). The namespace comes from the path, the token
from the header.

| Tool | = REST | Note |
|---|---|---|
| `remember` | POST /memories | `evidence` for Claude Code — `source_ref=claude-code:<project>`, `speaker=agent`; for the owner's words in the conversation — `speaker=owner` |
| `add_evidence` | POST …/evidence | "this was confirmed again" |
| `recall` | POST /recall | `tags` really filter (P7) |
| `get`, `list`, `history` | GET … | |
| `used`, `helpful` | POST …/used, …/helpful | Claude Code calls `used` when a record helped in its work |
| `confirm`, `pin` | POST …/confirm, …/pin | only tokens with `review` |
| `review_list`, `review_decide` | GET /review, POST /review/{id}/… | going through the clean-up right from Cowork |
| `sleep_report` | GET /sleep/latest | |
| `forget` | POST /forget | only tokens with `forget`; always a `dry_run` first |
| `stats` | GET /stats | |

Tool descriptions list the allowed `kind` values and example slots for the namespace (in English by default, in
Russian with `MEM_MCP_LANG=ru`).

## Functional requirements

| ID | Requirement | Co-auth. | Type |
|---|---|---|---|
| A-1 | REST and MCP are thin adapters over `mem.service`; there is no business logic in the adapters | 4 | DET |
| A-2 | The OpenAPI schema is kept in the repository; a change without updating the file fails CI | 4 | DET |
| A-3 | `remember` is idempotent by `(namespace, external_ref)`; a repeat with new evidence = `add_evidence` | 4 | DET |
| A-4 | Limits: `text` 4,000 characters, `quote` 200 | 4 | DET |
| A-5 | Only the owner's tokens have the `review` right (the agent's bot acts for the owner after a button press; a coding assistant is the owner) | 4 | DET |

## Acceptance criteria

- For every MCP tool, a test: the same result as its REST equivalent on the same data.
- `remember` without `evidence` → 400, no record.
- `recall` with `tags=[project:shop]` over MCP returns no records without the tag.
- `POST /forget` without `dry_run=false` changes nothing; with a wrong `confirm` → 400.
- A token without `review` on `POST /confirm` → 403.
