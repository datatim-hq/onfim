# REST API

**English** · [Русский](ru/rest-api.md)

All operations live under `/v1/`. The same operations are available over MCP ([mcp.md](mcp.md)); both call one
service layer, so filters, rights and errors behave the same. Concepts used below (kinds, evidence, slots, strength,
the review queue) are explained in [concepts.md](concepts.md).

Examples use `http://127.0.0.1:3120` (the address the Docker Compose setup publishes) and the namespace `alice.work`
created by `python -m mem.cli init --tenant acme --person alice`.

```bash
BASE=http://127.0.0.1:3120
AUTH="Authorization: Bearer <token>"
JSON="Content-Type: application/json"
```

## Authentication

Every `/v1/` request needs `Authorization: Bearer <token>`. Tokens are printed once by `mem.cli init` or
`mem.cli create-client`; only their hash is stored. A missing or unknown token, or a token whose principal was
deactivated, gets `401`.

A token belongs to a principal (a person, an agent acting for a person, or a service). What the request may do is the
intersection of:

- the **grants** of the person (or group) in the namespace, which give a role: `reader` → `read`; `contributor` →
  `read`, `write`; `curator` → `+review`, `forget`; `owner` → `+manage`;
- the token's **ceiling**: the namespaces and rights written into the token.

A namespace you have no access to returns `403` whether or not it exists. A record above your classification limit
returns `404`.

**Delegated clients** (tokens created with `--delegate-group`) must send `X-Mem-On-Behalf-Of: <person>` or
`X-Mem-On-Behalf-Of: group:<group>` on every request; without it the answer is `400`. Any other token sending this
header gets `400`.

### Local admin

The `admin` right works only for **direct** requests to the service. A request that carries an `X-Real-IP` or
`X-Forwarded-For` header is treated as remote and gets no admin powers at all, even with an admin token: the
built-in `admin` token then has no access to any namespace. Locally, a token with the `admin` right has every right
in every namespace of every organization, up to classification 3.

Your reverse proxy must therefore always set one of these headers (see
[operations.md](operations.md#running-behind-a-tls-reverse-proxy)). The admin token is configured with
`MEM_ADMIN_TOKEN`; the CLI talks to the database directly and does not need it.

Routes that require local admin: `POST /v1/_admin/sleep/run`, `POST /v1/_stand/clock`, `POST /v1/{ns}/import`.
`GET /v1/{ns}/export` accepts either local admin or `manage`.

## Errors

Errors are JSON:

```json
{"error": "forbidden", "message": "..."}
```

| Status | `error` | When |
|---|---|---|
| 400 | `bad_request` | invalid input: missing `text`/`kind`/`evidence`, unknown kind, kind not allowed by the namespace profile, text over 4000 or quote over 200 characters, a secret in the text, classification above your limit, a malformed body or query parameter |
| 401 | `unauthorized` | no token or unknown token |
| 403 | `forbidden` | no access to the namespace, or the right is missing |
| 404 | `not_found` | record, review item or sleep report not found (also for records above your classification) |
| 409 | `conflict` | slot conflict with an explicit `supersedes`; a review item already decided; a promotion already waiting |
| 503 | `embedder_unavailable` | the embedding model failed; nothing was written |

Rely on `error`; `message` is human-readable English text and may change between versions. Unknown paths return FastAPI's default
`404 {"detail": "Not Found"}`.

## Service routes (no token)

### `GET /healthz`

Liveness check. Returns `{"ok": true}`.

### `GET /status/{secret}`

Counters for monitoring, only if `MEM_STATUS_SECRET` is set and `{secret}` matches it; otherwise `404`. Returns
`dbOk`, `embedDim`, `modelId`, `time`, `clockOffsetS`, record counts per namespace and state (`memories`),
`strengthQuartiles` of current records, `lastLightSleep` and `lastDeepSleep` (`{at, status}`), and `reviewOpen`. No
texts.

## Records

### Record object

Reads return records like this (`get` also adds all `evidence` and the 20 latest strength `events`; `list` and
`recall` add up to three latest pieces of evidence):

```json
{
  "id": "6f1c...", "namespace": "alice.work", "kind": "gotcha",
  "text": "Next.js redirect via request.url goes to localhost behind the proxy; build the URL from the Host header",
  "subject": "project:shop", "slot": null, "tags": ["project:shop", "nextjs"], "importance": 2,
  "source": "alice-agent", "visibility": "shareable", "classification": 1,
  "occurred_at": null, "created_at": "2026-10-09T12:00:00+00:00",
  "state": "current", "valid_from": "2026-10-09T12:00:00+00:00", "valid_to": null,
  "supersedes_id": null, "duplicate_of": null, "promoted_from": null,
  "pinned": false, "archived_at": null, "archived_by": null,
  "strength": 0.55, "evidence_count": 1, "last_reinforced_at": "2026-10-09T12:00:00+00:00",
  "external_ref": null, "external_closed_at": null,
  "trust": "trusted", "flags": [],
  "evidence": [{"source_ref": "claude-code:shop", "source_scope": "dev", "speaker": "agent", "quote": null,
                "observed_at": "2026-10-09T12:00:00+00:00", "independent_key": "agent@dev", "speaker_principal": null}]
}
```

`strength` is the current value, with decay applied at the time of the request.

### `POST /v1/{ns}/memories` — remember

Right: `write`. Returns `201` and the record.

| Field | Required | Notes |
|---|---|---|
| `text` | yes | non-empty, ≤ 4000 characters; hidden characters are stripped, secrets refused |
| `kind` | yes | must be allowed by the namespace profile |
| `evidence` | yes | a list (or a single object) of evidence; see below |
| `subject` | no | |
| `slot` | no | a new record in an occupied slot becomes a `candidate` |
| `tags` | no | list of strings |
| `importance` | no | 1, 2 (default), 3 |
| `visibility` | no | `shareable` (default), `same_chat`, `owner_only` |
| `classification` | no | 0–3; default is the namespace default; above your limit → `400` |
| `occurred_at` | no | ISO 8601 |
| `external_ref` | no | idempotency key within the namespace |
| `supersedes` | no | id of a **current** record to replace explicitly |
| `source` | no | honoured only for local admin (imports); otherwise `source` is the client id |

Evidence fields: `source_ref`, `source_scope`, `observed_at` (required); `speaker`, `quote` (≤ 200),
`speaker_principal` (a principal of the namespace's organization), `forwarded_from`, `independent_key` (optional).

```bash
curl -sS -X POST "$BASE/v1/alice.work/memories" -H "$AUTH" -H "$JSON" -d '{
  "text": "Deploys go through make release",
  "kind": "convention",
  "subject": "project:shop",
  "slot": "project:shop#deploy_process",
  "tags": ["project:shop"],
  "evidence": [{"source_ref": "claude-code:shop", "source_scope": "dev", "speaker": "owner",
                "observed_at": "2026-10-09T12:00:00Z", "quote": "we always deploy with make release"}]
}'
```

Extra fields in the response, when they apply:

- `slot_current_id` — the slot was occupied and this record is a candidate (the value is `"hidden"` if the current
  record is above your classification limit);
- `idempotent: true` and `evidence_results` — a record with this `external_ref` already existed; the evidence was added
  to it;
- `reinforced` and `evidence_results` — the same claim was already current in the slot; the evidence was added to it.

A rule kind (`decision`, `convention`, `gotcha`) from untrusted evidence, or from a non-curator in a team namespace, is
stored as a `candidate` with a `rule_confirm` review item.

### `GET /v1/{ns}/memories` — list

Right: `read`. Lists records without search. Query parameters:

| Parameter | Notes |
|---|---|
| `kind`, `subject`, `slot` | exact match; repeat the parameter to match any of several values. `subject` follows aliases |
| `tags` | repeat or comma-separate; `tags_mode=any` (default) or `all` |
| `state` | `current` (default), `candidate`, `superseded`, `archived`, or `all`; repeatable |
| `include_candidates`, `include_archived` | `true` adds those states to the default `current` |
| `as_of` | ISO time: records that were in force at that moment |
| `created_after`, `created_before` | ISO times |
| `audience` | `chat:<id>`: only records that may be said in that chat |
| `min_strength` | number |
| `sort` | `created_at` (default, newest first) or `strength` |
| `limit` | 1–200, default 20 |
| `cursor` | `next_cursor` from the previous page |

Returns `{"items": [...], "next_cursor": "..." | null}`. Duplicates hidden by the sleep are never listed.

```bash
curl -sS "$BASE/v1/alice.work/memories?tags=project:shop&kind=gotcha&sort=strength&limit=50" -H "$AUTH"
```

### `GET /v1/{ns}/memories/{id}` — get

Right: `read`. The record with all evidence and the 20 latest strength events (`{at, type, delta, actor}`).

### `POST /v1/{ns}/memories/{id}/evidence` — add evidence

Right: `write`. Body: one evidence object. Independent evidence reinforces by 0.30, a repeat by 0.05; a repeated
`source_ref` is ignored. Trusted evidence makes the record trusted.

```bash
curl -sS -X POST "$BASE/v1/alice.work/memories/<id>/evidence" -H "$AUTH" -H "$JSON" -d '{
  "source_ref": "https://github.com/acme/shop/pull/12", "source_scope": "repo",
  "observed_at": "2026-10-10T09:00:00Z"}'
```

Returns `{"id", "changed": true, "independent", "strength_before", "strength"}` or `{"id", "changed": false, "reason"}`.

### `GET /v1/{ns}/memories/{id}/history` — history

Right: `read`. All versions in the record's slot (or, without a slot, its replacement chain), oldest first, and the
transitions (`supersede`, `promote`, `revert_supersede`, `archive`, `unarchive`) with time, actor and reason. Returns
`{"slot", "records": [...], "transitions": [...]}`.

### `POST /v1/{ns}/recall` — search

Right: `read`. Hybrid search (vector + full text) with the ranking formula from [concepts.md](concepts.md#strength-and-decay).

| Field | Notes |
|---|---|
| `query` | the question in your own words; empty → `list` sorted by strength |
| `limit` | 1–50, default 10 |
| `explain` | `true` adds `explain: {sem, lex, strength, prior, score}` per item |
| filters | the same as `list`: `tags`, `tags_mode`, `kind`, `subject`, `slot`, `state`, `include_candidates`, `include_archived`, `as_of`, `created_after`, `created_before`, `audience`, `min_strength` |

```bash
curl -sS -X POST "$BASE/v1/alice.work/recall" -H "$AUTH" -H "$JSON" \
  -d '{"query": "how do we deploy the shop?", "tags": ["project:shop"], "explain": true}'
```

Returns `{"items": [...], "query": "..."}`. Each item has `score` and `contested`; a contested current record also has
`candidate_id`. The top 5 results get a small reinforcement (at most once a day per record).

### `POST /v1/recall` — search across layers

Right: `read` in each namespace searched. Searches every namespace you can read, or a given list, and merges the
results with a layer weight (personal 1.0, group 0.95, organization 0.9).

| Field | Notes |
|---|---|
| `query`, `limit`, filters | as in `recall` |
| `namespaces` | list of namespace ids; any you cannot read → `403` |
| `scopes` | subset of `personal`, `group`, `tenant` (list or comma-separated) |

```bash
curl -sS -X POST "$BASE/v1/recall" -H "$AUTH" -H "$JSON" -d '{"query": "release process", "scopes": ["personal", "group"]}'
```

Returns `{"items": [...], "query", "namespaces": [<searched>]}`; every item has `layer: {namespace, scope}`.

## Signals

All return `{"id", "event", "strength_before", "strength"}` and take no body.

| Route | Right | Effect |
|---|---|---|
| `POST /v1/{ns}/memories/{id}/used` | `write` | the record was used in work or an answer (+0.15) |
| `POST /v1/{ns}/memories/{id}/helpful` | `write` | the record was helpful (+0.25) |
| `POST /v1/{ns}/memories/{id}/confirm` | `review` | owner confirmation (+0.50); also adds owner evidence and makes the record trusted. Call only on a human's direct word |

## Record management

| Route | Right | Effect |
|---|---|---|
| `POST /v1/{ns}/memories/{id}/pin` | `review` | the record never decays or gets forgotten; returns the record |
| `POST /v1/{ns}/memories/{id}/unpin` | `review` | undoes `pin` |
| `POST /v1/{ns}/memories/{id}/close` | `write` | for `commitment_mirror` and `crm_mirror` only: the external object is closed, the record starts to decay |
| `POST /v1/{ns}/memories/{id}/archive` | `write` | hides the record from search; reversible |
| `POST /v1/{ns}/memories/{id}/unarchive` | `write` | restores it with strength 0.30 to its previous state; a record that was current comes back as a `candidate` if its slot is now taken |
| `POST /v1/{ns}/memories/{id}/revert-supersede` | `review` | within 30 days of a replacement: the old record becomes current again, this one a candidate. Returns `{"current", "candidate"}` |
| `DELETE /v1/{ns}/memories/{id}?mode=purge` | `forget` | deletes the record, its evidence, events and vector. Returns `{"purged": 1, "id"}` |
| `DELETE /v1/{ns}/memories/{id}?mode=archive` | `write` | same as `archive` |

`mode` defaults to `purge`.

### `POST /v1/{ns}/forget` — bulk forget

Right: `forget`.

| Field | Notes |
|---|---|
| `subject`, `slot`, `kind`, `tags`, `source_ref` | at least one is required; `tags` must all be present; `source_ref` matches every record that has this source among its evidence, so everything taken from one leaked message can be removed at once |
| `mode` | `archive` (default) or `purge` |
| `dry_run` | default `true`: only counts |
| `confirm` | with `dry_run: false`, must equal the number of matching records |

```bash
# 1. see what would go
curl -sS -X POST "$BASE/v1/alice.work/forget" -H "$AUTH" -H "$JSON" -d '{"subject": "project:old", "mode": "purge"}'
# 2. apply, confirming the count from step 1
curl -sS -X POST "$BASE/v1/alice.work/forget" -H "$AUTH" -H "$JSON" \
  -d '{"subject": "project:old", "mode": "purge", "dry_run": false, "confirm": 7}'
```

Returns `{"mode", "matched", "dry_run", "sample": [up to 10 {id, kind, subject}]}`, plus `applied` when applied.
With `purge`, review queue items that mention a deleted record are deleted too, since they hold its quotes.

## Team memory

### `POST /v1/{ns}/memories/{id}/promote` — propose a record to another layer

Right: `write` in the source and in the target namespace, both in one organization. The record must be current.

| Field | Notes |
|---|---|
| `to` | target namespace |
| `share_quotes` | default `true`; `false` leaves quotes out of the proposal |
| `note` | free text for the curator |

Returns `{"status": "open", "to", "review_id"}`, or `{"status": "accepted", "to", "memory_id", "state"}` when the
target's `promotion_auto` setting lists the record's kind. A second proposal while one is open → `409`.

### `POST /v1/{ns}/aliases` — subject alias

Right: `write`. Body `{"alias": "person:tg123", "canonical": "crm:contact:77"}`. The alias applies to the whole
organization of the namespace; `subject` filters follow it both ways. Returns `{"alias", "canonical", "tenant"}`.

## Review queue

### `GET /v1/{ns}/review?status=open`

Right: `review`. `status`: `open` (default), `accepted`, `rejected`, `expired`, `all`. Returns
`{"items": [{"id", "type", "status", "created_at", "namespace", "proposed_by", "proposal", "records": [{"id", "kind",
"text", "slot", "state", "quote"}]}]}`. A `promotion` item carries the proposed record (text, kind, evidence, classification) in `proposal`. Items that involve records above your classification are not shown.

### `GET /v1/review?status=open`

The same across every namespace where you have `review`; each item has `layer: {namespace, scope}`.

### `POST /v1/{ns}/review/{id}/accept` and `POST /v1/{ns}/review/{id}/reject`

Right: `review`. For a `contradiction`, `accept` needs `{"winner": "<id of the correct record>"}`. What each decision
does per item type is in [concepts.md](concepts.md#the-review-queue). An item that is no longer open → `409`.

```bash
curl -sS -X POST "$BASE/v1/alice.work/review/12/accept" -H "$AUTH" -H "$JSON" -d '{"winner": "<id>"}'
```

## Sleep

### `GET /v1/{ns}/sleep/latest?kind=light|deep`

Right: `read`. The report of the latest finished run of that kind (real runs are preferred over dry runs), for this
namespace: `job_id`, `status`, `dry_run`, `started_at`, `finished_at`, `stages` (status and counts), `replaced`,
`forgotten`, `duplicates_merged`, `candidates_expired`, `contradictions_open`; for deep sleep also `slot_suggestions`,
`stale`, `summaries_applied`, `summaries_rejected`, `model_items_skipped`. `404` if there has been no run.

### `GET /v1/{ns}/sleep/{job_id}`

Right: `read`. The report of one run.

### `POST /v1/_admin/sleep/run`

Local admin only. Runs a sleep in the API process and returns its result.

| Field | Notes |
|---|---|
| `kind` | `light` (default) or `deep` |
| `dry_run` | default `true`; pass `false` to apply |

Returns `{"job_id", "status", "dry_run", "stages"}`, or `{"status": "SKIPPED", "reason"}` if a sleep of that kind is
already running.

```bash
curl -sS -X POST "$BASE/v1/_admin/sleep/run" -H "Authorization: Bearer <admin-token>" -H "$JSON" \
  -d '{"kind": "light", "dry_run": true}'
```

## Maintenance

### `GET /v1/{ns}/stats`

Right: `read`. `{"namespace", "counts": {kind: {state: n}}, "strength_current": {"n", "q1", "median", "q3", "avg"}}`.

### `GET /v1/{ns}/export`

Right: `manage` (the namespace owner's role, with `manage` in the token) or local admin. Streams NDJSON
(`application/x-ndjson`): one record per line in every state, with all evidence and `stored_strength`.

```bash
curl -sS "$BASE/v1/alice.work/export" -H "Authorization: Bearer <admin-token>" > alice.work.jsonl
```

### `POST /v1/{ns}/import`

Local admin only. Body `{"records": [...], "dry_run": true}`; each record has the same fields as `remember` (its
`source` is kept). With `dry_run` (the default) only the fields are checked and `{"dry_run", "records", "problems"}`
is returned; if any record has a problem nothing is written. A repeat with the same `external_ref` adds evidence
instead of duplicating.

### `POST /v1/_stand/clock`

Local admin only, and only when the service runs with `MEM_STAND=1` (a test stand). Shifts the service clock so decay
and forgetting can be tested in minutes. Body: one of `{"offset_s": N}`, `{"advance_s": N}`, `{"advance_days": N}`.
Returns `{"offset_s", "now"}`.

## Other paths

- `/mcp/{namespace}/` — the MCP endpoint ([mcp.md](mcp.md)).
- `/openapi.json` — FastAPI's generated schema. Request bodies are untyped there; this page is the reference.
