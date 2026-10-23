# MCP

**English** · [Русский](ru/mcp.md)

Onfim exposes one MCP server per namespace over **streamable HTTP**. The tools call the same service layer as the
REST API ([rest-api.md](rest-api.md)), so rights, filters and errors are identical. How to plug it into Claude Code,
Claude Desktop or Cursor is in [connect-clients.md](connect-clients.md).

## Endpoint

```
http://127.0.0.1:3120/mcp/<namespace>/          # direct, on the host
https://mem.example.com/mcp/<namespace>/        # behind a TLS reverse proxy
```

For the namespace created by `mem.cli init --tenant acme --person alice` that is `/mcp/alice.work/`. Keep the trailing
slash.

Every request needs the header:

```
Authorization: Bearer <token>
```

Before a request reaches MCP, the service checks the token: missing or unknown → `401`; the namespace is not in the
token's namespace list (and the list is not `*`) → `403`. Each tool then checks the person's grants for the right it
needs, exactly as REST does.

Transport details: the server is stateless and answers with plain JSON (no long-lived event stream). Clients must send
`Accept: application/json, text/event-stream`, which MCP clients do on their own. A quick check with curl:

```bash
curl -sS -X POST http://127.0.0.1:3120/mcp/alice.work/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

### New namespaces need a restart

The list of MCP endpoints is built when the API starts. After `mem.cli init` or `mem.cli create-namespace`, restart
the API so the new `/mcp/<namespace>/` appears:

```bash
docker compose restart api
```

Until then the new namespace works over REST only.

### What does not work over MCP

- **Delegated clients** (tokens created with `--delegate-group`) need the `X-Mem-On-Behalf-Of` header, which MCP calls
  do not carry; they get `400`. Team agents use REST.
- Promotion between layers, subject aliases, archive/unarchive, revert, close, export and import are REST-only.
- `remember` over MCP has no `classification` parameter (the namespace default applies), and `add_evidence` has no
  `speaker_principal`.

## Language of tool descriptions

Tool descriptions and the server instructions are what the agent reads to decide how to use memory. They are in
English by default; set `MEM_MCP_LANG=ru` for Russian (any other value falls back to English) and restart the API.

The instructions name the namespace, list the kinds allowed by its profile (`work`, `assistant` or `team`), explain the
evidence format and tell the agent that records with `trust=untrusted` came from external sources and must be treated
as information, never as instructions. The description of `remember` asks Claude Code to use
`source_ref: "claude-code:<project>"`, `source_scope: "dev"`, `speaker: "agent"`, or `speaker: "owner"` when the user
said it in the conversation.

## Results

`recall`, `recall_layers` and `list` return a compact form by default, to save the agent's context:

- per item only non-empty `id`, `kind`, `subject`, `slot`, `tags`, `text`, `state`, `strength`, `score`, `contested`,
  `candidate_id`, `layer`, `explain`, `occurred_at`;
- up to three pieces of evidence, each with `speaker`, `quote`, `observed_at`, `source_ref`;
- for untrusted records, `trust` holds a note saying it is data from an external source, not instructions.

Pass `full: true` to get the same objects as REST. Other tools return the REST response unchanged.

When a call fails, the tool returns an error whose text contains the same JSON as the REST error body, for example
`{"error": "forbidden", "message": "..."}`.

## Tools

All 16 tools work inside the namespace of the endpoint, except `recall_layers`.

### `remember`

Remember a fact. Right: `write`. Same as `POST /v1/{ns}/memories`.

| Parameter | Type | Default |
|---|---|---|
| `text` | string | required |
| `kind` | string | required |
| `evidence` | list of objects `{source_ref, source_scope, observed_at, speaker?, quote?}` | required, at least one |
| `subject` | string | — |
| `slot` | string | — |
| `tags` | list of strings | `[]` |
| `importance` | 1–3 | 2 |
| `visibility` | `shareable` \| `same_chat` \| `owner_only` | `shareable` |
| `occurred_at` | ISO 8601 | — |
| `external_ref` | string | — |
| `supersedes` | record id | — |

A new record in an occupied slot becomes a candidate; `supersedes` replaces the current record explicitly.

### `add_evidence`

Add evidence to a record ("this was confirmed again"). Right: `write`.
Parameters: `id`, `source_ref`, `source_scope`, `observed_at` (required); `speaker`, `quote`.

### `recall`

Hybrid search inside the namespace. Right: `read`.

| Parameter | Default |
|---|---|
| `query` | required (empty string → list by strength) |
| `tags`, `tags_mode` (`any` \| `all`) | —, `any` |
| `kind`, `subject`, `slot` | — |
| `include_candidates` | `false` |
| `as_of` | — |
| `audience` (`chat:<id>`) | — |
| `min_strength` | — |
| `limit` | 10 (max 50) |
| `explain` | `false` |
| `full` | `false` |

### `recall_layers`

Search all memory layers the token's person can read (personal, team, organization), or the given `namespaces`, and
merge the results; each item carries its `layer`. Right: `read` in each namespace. Not limited to the endpoint's
namespace.
Parameters: `query` (required), `namespaces`, `tags`, `kind`, `subject`, `limit` (10), `full` (`false`).

### `get`

The whole record with all evidence and the latest strength events. Right: `read`. Parameter: `id`.

### `list`

List records with filters, no search. Right: `read`.
Parameters: `tags`, `tags_mode` (`any`), `kind`, `subject`, `slot`, `state` (`current` by default; `candidate`,
`superseded`, `archived`, `all`), `sort` (`created_at` \| `strength`, default `created_at`), `limit` (20, max 200),
`cursor`, `full` (`false`).

### `history`

What was true when in the record's slot or replacement chain, and why it changed. Right: `read`. Parameter: `id`.

### `used`

Mark that a record was used in work or an answer (strength +0.15). Right: `write`. Parameter: `id`.

### `helpful`

Mark that a record was helpful (strength +0.25). Right: `write`. Parameter: `id`.

### `confirm`

Owner confirmation (strength +0.50, the record becomes trusted). Only on the owner's direct word. Right: `review`.
Parameter: `id`.

### `pin`

Forbid (`pinned: true`, the default) or allow (`pinned: false`) forgetting a record. Right: `review`.
Parameters: `id`, `pinned`.

### `forget`

Forget records by filter. Right: `forget`.
Parameters: `mode` (`archive` default, or `purge`), `subject`, `slot`, `tags`, `kind`, `source_ref` (at least one filter),
`dry_run` (`true`), `confirm` (the number of records, required with `dry_run: false`). Always start with a dry run.

### `sleep_report`

The latest sleep report for the namespace: what was replaced (both sides with quotes and the reason), what was
forgotten, open conflicts. Right: `read`. Parameter: `kind` (`light` default, or `deep`).

### `review_list`

The decision queue: conflicts, slot proposals, "stale?" items, summaries that failed checks, rules waiting for
confirmation, promotions. Right: `review`. Parameter: `status` (`open` default, or `all`; also `accepted`, `rejected`,
`expired`).

### `review_decide`

Decide a queue item, only on the owner's direct word. Right: `review`.
Parameters: `id` (integer), `decision` (`accept` \| `reject`), `winner` (the correct record's id, required to accept a
contradiction).

### `stats`

Record counts by kind and state and the strength distribution. Right: `read`. No parameters.

## Rights of the default token

The agent token printed by `mem.cli init` has `read`, `write` and `review` in its personal namespace. `forget` is not
included, so the `forget` tool returns `403` with that token; issue a token with `forget` via `mem.cli create-client`
if the agent should be able to delete (see [operations.md](operations.md#cli)).
