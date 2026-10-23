# Operations

**English** · [Русский](ru/operations.md)

How to run Onfim in production: configuration, the admin CLI, the reverse proxy, the sleep worker, alerts, the
deep-sleep model, backups and upgrades.

## What runs

`docker compose up -d` starts three containers (Compose project `mem`):

| Service | What it does | Memory limit |
|---|---|---|
| `postgres` | Postgres 16 with pgvector; not published to the host | 512 MB |
| `api` | REST and MCP on one port, published as `127.0.0.1:3120`; computes embeddings locally; runs migrations at start | 1536 MB |
| `worker` | the same image running `python -m mem.jobs`: the nightly and weekly sleep | 1280 MB |

Volumes: `mem_pg` (database), `mem_models` (the embedding model, downloaded from Hugging Face on first start, so the
first start needs outbound access and takes a while), `mem_exports` (nightly JSONL exports written by the worker).
The `api` container also joins a Docker network `mem_clients` under the alias `mem-api`, so other containers can reach
it at `http://mem-api:3120` without going through the proxy; Postgres is not on that network.

## Environment variables

Put them in `.env` next to `docker-compose.yml` (start from `.env.example`; never commit `.env`). Both `api` and
`worker` read it.

| Variable | Default | Meaning |
|---|---|---|
| `MEM_DB_PASSWORD` | — (required) | Postgres password; used by Compose to build `MEM_DATABASE_URL` |
| `MEM_DATABASE_URL` | `postgresql://mem:mem@localhost:5432/mem` | database URL; Compose sets it for you |
| `MEM_EMBED_MODEL` | `intfloat/multilingual-e5-small` | embedding model (fastembed/ONNX, local). `intfloat/multilingual-e5-base` (768) also works; `fake` is a deterministic embedder for tests |
| `MEM_EMBED_DIM` | `384` | vector dimension; must match the model |
| `MEM_HOST` | `0.0.0.0` | bind address inside the container |
| `MEM_PORT` | `3120` | port inside the container (the Compose file publishes 3120) |
| `MEM_ADMIN_TOKEN` | unset | if set, the service creates the `admin` client with this token at every start (see [Local admin](#local-admin)) |
| `MEM_STATUS_SECRET` | unset | enables `GET /status/<secret>`; without it the route is `404` |
| `MEM_MCP_LANG` | `en` | language of MCP tool descriptions: `en` or `ru` |
| `MEM_TZ` | `UTC` | time zone of the sleep schedule (an IANA name such as `Europe/Berlin`), read by the worker |
| `MEM_TRUSTED_SCOPES` | `owner,dev,session,repo,file,import,cowork,code,claude,crm` | evidence `source_scope` prefixes treated as the client's own work (see [concepts.md](concepts.md#trust)) |
| `MEM_EXPORT_DIR` | unset (`/exports` in the worker) | where the light sleep writes JSONL exports; unset → the export stage is skipped |
| `MEM_DB_APP_ROLE` | `mem_app` | database role the service switches to so row-level security applies; set to an empty value only if you know why |
| `MEM_ALERT_WEBHOOK_URL` | unset | alerts: POST `{"text": "..."}` to this URL |
| `MEM_TG_BOT_TOKEN`, `MEM_TG_CHAT_ID` | unset | alerts to Telegram (both needed) |
| `MEM_TG_PROXY` | unset | `socks5://host:port`: send Telegram alerts through this SOCKS5 proxy |
| `MEM_ANTHROPIC_API_KEY` | unset | key for Anthropic models in deep sleep |
| `MEM_DEEP_MODEL_REASON` | unset | deep-sleep model for contradictions and slot suggestions; unset → those stages are `DISABLED` |
| `MEM_DEEP_MODEL_SUMMARY` | unset | deep-sleep model for episode summaries; unset → that stage is `DISABLED` |
| `MEM_OPENAI_BASE_URL` | unset | OpenAI-compatible endpoint inside your network, e.g. `http://llm.internal:8000/v1` |
| `MEM_OPENAI_API_KEY` | unset | sent as `Authorization: Bearer` to that endpoint, if set |
| `MEM_OPENAI_IN_CONTOUR_MODELS` | unset | comma-separated model names allowed on the `openai:` route |
| `MEM_STAND` | `0` | `1` turns on test-stand mode: the clock can be shifted. Never in production |
| `MEM_CLOCK_OFFSET` | `0` | initial clock shift in seconds, only with `MEM_STAND=1` |

The image also sets `FASTEMBED_CACHE_PATH=/models` and `HF_HOME=/models/hf` so the model lands in the `mem_models`
volume.

**The embedding model is fixed per database.** The first start records the model and dimension; the service refuses to
start if they change later. There is no built-in re-embedding command, so choose the model before the first start.

## Local admin

The `admin` right is honoured only for requests made **directly** to the service. A request with an `X-Real-IP` or
`X-Forwarded-For` header is treated as remote, and an admin token then has no access to anything. This is a header
check, not an IP check:

- your reverse proxy must always set `X-Real-IP` (or `X-Forwarded-For`), or remote callers would be "local";
- any process that can reach port 3120 directly, including containers on the `mem_clients` network, counts as local.
  Never hand an admin token to such a process; give it a normal client token.

`MEM_ADMIN_TOKEN` creates a client `admin` (principal `admin` in an organization `system`) with every right in every
namespace. Generate it with `openssl rand -hex 32`. The token is written at each start: change the value and restart
to rotate it. Removing the variable does not delete the stored token, so rotate it to a fresh random value instead.

Admin-only REST routes: `POST /v1/_admin/sleep/run`, `POST /v1/{ns}/import`, `GET /v1/{ns}/export` (also allowed to
namespace owners), `POST /v1/_stand/clock` (only with `MEM_STAND=1`).

## CLI

Organizations, people, groups, namespaces, grants and tokens are managed with `python -m mem.cli`, run inside the
`api` container. The CLI talks to the database directly and needs no token.

```bash
docker compose exec api python -m mem.cli <command> [options]
```

Commands are idempotent where possible: creating something that exists leaves it as it is (a principal's clearance
and display name are updated). An ID that already belongs to another organization, or to a principal of another kind
or acting for someone else, or a namespace with another organization or owner, is an error: the command prints the
reason to stderr and exits with code 1, so an install script stops there. An agent can act only for a person of its
own organization.

| Command | Arguments and flags | What it does |
|---|---|---|
| `init` | `--tenant ID` `--person ID` `[--name TEXT]` `[--ns ID]` `[--agent ID]` `[--profile work\|assistant\|team]` | first run: organization, person (clearance 3), personal namespace `<person>.<profile>` with an `owner` grant up to level 3, an agent `<person>-agent` acting for the person, and its token (`read,write,review` on that namespace). Prints the token once. Re-running re-issues the token |
| `create-tenant` | `ID NAME` | an organization |
| `create-principal` | `ID --tenant ID` `[--kind user\|agent\|service]` (default `user`) `[--on-behalf-of ID]` (required for agents) `[--clearance 0-3]` (default 1) `[--name TEXT]` | a person, an agent or a service |
| `create-group` | `ID --tenant ID --name TEXT` `[--parent ID]` `[--kind team\|org_unit\|project\|role]` (default `team`) | a group, optionally nested |
| `add-member` | `GROUP PRINCIPAL` `[--role member\|curator]` | adds a principal to a group (the member role grants nothing by itself) |
| `create-namespace` | `ID --tenant ID --scope personal\|group\|tenant` `[--owner-principal ID]` `[--owner-group ID]` `[--classification 0-3]` (default 1) `[--profile work\|assistant\|team]` (default `work`) `[--description TEXT]` | a namespace (layer). Restart the API afterwards so MCP serves it |
| `grant` | `NS principal\|group\|tenant SUBJECT reader\|contributor\|curator\|owner` `[--max-class 0-3]` (default 1) `[--expires ISO-DATE]` | gives a role in a namespace; re-granting updates it |
| `revoke` | `NS SUBJECT_TYPE SUBJECT_ID` | removes a grant |
| `create-client` | `--id ID --ns NS[,NS...]\|* --rights LIST` `[--principal ID]` (default = `--id`) `[--delegate-group GROUP]` | creates a token, or replaces the token of an existing client. Rights: `read,write,review,forget,manage,admin`. Prints the token once |
| `check-client` | `NS` `[--need LIST]` (default `read`), token on stdin | what the token can do in the namespace, as an external request sees it (organization, rights, classification ceiling). Exits with 1 if any right from `--need` is missing or the token is unknown. Use it to verify a token before handing it to an agent: `docker compose exec -T api python -m mem.cli check-client alice.work --need read,write < token.txt` (`-T` passes stdin through) |
| `list-clients` | — | id, principal, namespaces, rights, last seen |
| `offboard` | `PRINCIPAL` `[--purge-after-days N]` (default 30) | a person leaves: they and their agents are deactivated, their tokens deleted, group memberships removed, personal namespaces scheduled for deletion by the light sleep |
| `purge-namespace` | `NS --yes` | deletes every record of a namespace now (irreversible) |
| `forget-subject` | `TENANT SUBJECT` `[--confirm N]` | deletes every record about a subject and its aliases across the organization. Without `--confirm` it is a dry run printing the count; pass that count to apply |

A token's namespaces and rights are a ceiling: what it can actually do also depends on the grants of the person it
belongs to. A team example:

```bash
cli="docker compose exec api python -m mem.cli"
$cli create-tenant acme "ACME"
$cli create-principal alice --tenant acme --clearance 2 --name "Alice"
$cli create-principal bob --tenant acme --name "Bob"
$cli create-group acme/eng --tenant acme --name Engineering
$cli add-member acme/eng alice; $cli add-member acme/eng bob
$cli create-namespace acme.eng --tenant acme --scope group --owner-group acme/eng --profile team
$cli grant acme.eng group acme/eng contributor --max-class 1
$cli grant acme.eng principal alice curator --max-class 2
$cli create-principal bob-cc --tenant acme --kind agent --on-behalf-of bob
$cli create-client --id bob-cc --ns acme.eng --rights read,write
docker compose restart api
```

An agent token with `forget` (to let the `forget` tool work) is issued the same way:
`create-client --id alice-agent --ns alice.work --rights read,write,review,forget` (this replaces the old token).
Exporting a namespace over REST needs a token with `manage` whose person has the `owner` role there, or local admin.

Namespace settings without a CLI command are kept in `namespaces.settings` (JSON). For example, to accept promotions
of some kinds into a team layer without a curator:

```bash
docker compose exec postgres psql -U mem -d mem -c \
  "UPDATE namespaces SET settings = settings || '{\"promotion_auto\": [\"topic_fact\"]}' WHERE id = 'acme.eng'"
```

## Running behind a TLS reverse proxy

Keep port 3120 bound to `127.0.0.1` and publish only `/v1/` and `/mcp/` through a proxy with TLS. The proxy **must**
set `X-Real-IP` so that requests from outside are never treated as local admin.

### nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name mem.example.com;

    ssl_certificate     /etc/letsencrypt/live/mem.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mem.example.com/privkey.pem;

    client_max_body_size 2m;

    # admin and stand routes are local-only anyway; do not publish them
    location /v1/_ { return 404; }

    location ~ ^/(v1|mcp)/ {
        proxy_pass http://127.0.0.1:3120;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }

    # optional monitoring endpoint; the secret is part of the path
    # location /status/ { proxy_pass http://127.0.0.1:3120; proxy_set_header X-Real-IP $remote_addr; }

    location / { return 404; }
}

server {
    listen 80;
    server_name mem.example.com;
    return 301 https://$host$request_uri;
}
```

### Caddy

Caddy gets certificates on its own and adds `X-Forwarded-For` by default; the example sets `X-Real-IP` explicitly as
well.

```caddyfile
mem.example.com {
    handle /v1/_* {
        respond 404
    }
    handle /v1/* {
        reverse_proxy 127.0.0.1:3120 {
            header_up X-Real-IP {remote_host}
        }
    }
    handle /mcp/* {
        reverse_proxy 127.0.0.1:3120 {
            header_up X-Real-IP {remote_host}
            flush_interval -1
        }
    }
    handle {
        respond 404
    }
}
```

Check from outside that an admin token is refused: `curl -X POST https://mem.example.com/v1/<ns>/import ...` must
return `403` (or `404` for `/v1/_admin/...` with the configs above).

## The worker and the sleep schedule

The `worker` container runs the sleep jobs at fixed local times in the time zone set by `MEM_TZ` (default `UTC`):

| Job | When | Notes |
|---|---|---|
| light sleep | every day at 03:30 | deterministic, no model; uses about 60 MB |
| deep sleep | Sundays at 04:00 | loads the embedding model for the run (summaries are new records) and calls the deep-sleep model |

The times themselves are fixed in `mem/jobs.py`; pick `MEM_TZ` so that 03:30 falls in your quiet hours. A scheduled job is skipped if a real run of the same
kind already finished that day with `OK` or `PARTIAL`. What each stage does is in [concepts.md](concepts.md#sleep).

Run a sleep by hand:

```bash
docker compose exec worker python -m mem.jobs light-now --dry-run   # counts only, nothing changes
docker compose exec worker python -m mem.jobs light-now
docker compose exec worker python -m mem.jobs deep-now --dry-run
```

The command prints the result and exits with `0` when the status is `OK` (or `SKIPPED` because a run is in progress).
A local admin can also run a sleep through REST (`POST /v1/_admin/sleep/run`); it then runs inside the `api`
container, where `MEM_EXPORT_DIR` is not set, so the export stage is skipped.

Read reports with `GET /v1/<ns>/sleep/latest?kind=light|deep` or the MCP tool `sleep_report`.

**Disabling stages.** Any stage of either sleep can be switched off without a deploy; it is then reported as
`DISABLED`. Model stages whose model is not configured are already `DISABLED` on their own. To switch off the model
stages even with models configured:

```bash
docker compose exec postgres psql -U mem -d mem -c \
  "INSERT INTO meta(key, value) VALUES ('sleep_disabled_stages', 'contradictions,slot_suggestions,summaries')
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
```

Stage names: light — `decay`, `dedup_exact`, `promote`, `forget`, `retention`, `review_expiry`, `maintenance`,
`export`; deep — `contradictions`, `slot_suggestions`, `stale`, `summaries`.

**Exports.** With `MEM_EXPORT_DIR` set (the worker has `/exports`), the light sleep writes `<namespace>-YYYYMMDD.jsonl`
per namespace and keeps the 14 latest per namespace. Copy them out with `docker compose cp worker:/exports ./exports`.
They hold records and evidence, not the full database; use `pg_dump` for backups.

## Deep-sleep model routes

The deep sleep uses two models: `MEM_DEEP_MODEL_REASON` (contradictions, slot suggestions) and
`MEM_DEEP_MODEL_SUMMARY` (episode summaries). There is no default model: a stage whose model is not set is `DISABLED`
and nothing is sent anywhere, even if an API key is present. The route is chosen by a prefix in the model name:

| Model name | Route | Needs |
|---|---|---|
| `claude-...` (no prefix) or `anthropic:claude-...` | Anthropic Messages API | `MEM_ANTHROPIC_API_KEY` |
| `openai:<model>` | an OpenAI-compatible `chat/completions` endpoint inside your network | `MEM_OPENAI_BASE_URL`, optional `MEM_OPENAI_API_KEY`, and `<model>` listed in `MEM_OPENAI_IN_CONTOUR_MODELS` |
| anything else (`yandex:`, `gigachat:`, unknown prefixes, a name without prefix that is not `claude...`) | not supported | — |

Example with a model on your own inference server:

```bash
MEM_OPENAI_BASE_URL=http://llm.internal:8000/v1
MEM_OPENAI_API_KEY=<key-if-your-server-needs-one>
MEM_OPENAI_IN_CONTOUR_MODELS=my-model
MEM_DEEP_MODEL_REASON=openai:my-model
MEM_DEEP_MODEL_SUMMARY=openai:my-model
```

Rules enforced by the code:

- Record texts would leave your perimeter on a cloud route, and Onfim has no masking, so only Anthropic and models you
  host yourself are allowed. `MEM_OPENAI_BASE_URL` must be `http(s)://host[:port]/path` without credentials or query,
  and is refused if it points at a known cloud provider (OpenAI, Azure OpenAI, Anthropic, OpenRouter, DeepSeek,
  Mistral, Google, Groq, Together, Yandex, Sber). Proxy environment variables are ignored for this endpoint.
- The model must answer through a forced tool call that matches a JSON schema (temperature 0 on the `openai:` route);
  your server must support tool calling. An invalid answer is a stage error, never an empty proposal. Requests are
  retried up to three times on timeouts and 429/5xx.
- Misconfiguration (missing key, model not in the list, bad URL) is detected before the first call: the model stages
  end in error, the job is `PARTIAL`, an alert is sent and nothing is sent to any model.
- Records with classification 3 or `visibility: owner_only` are never sent, whatever the route.

## Alerts

An alert is sent when a sleep job finishes with a status other than `OK` (dry runs included), when the model returned
unusable items that had to be skipped, and when a scheduled job crashes. Every alert is logged as a warning
(`ALERT: ...`); in addition:

- `MEM_ALERT_WEBHOOK_URL` — POST with JSON `{"text": "..."}`. Works with Slack and Mattermost incoming webhooks or your
  own receiver.
- `MEM_TG_BOT_TOKEN` and `MEM_TG_CHAT_ID` — a message via the Telegram Bot API. If Telegram is reachable from your
  host only through a tunnel, set `MEM_TG_PROXY=socks5://host:port`; a malformed value stops Telegram alerts (logged)
  instead of sending them around the proxy.

Both can be set. Texts are in English and cut to 3500 characters. A failed delivery is logged and never
breaks the sleep. Alerts are sent by the process that ran the job (normally the worker), so set these variables in
`.env`, which both containers read.

## Monitoring

- `GET /healthz` → `{"ok": true}`; the Compose healthcheck uses it (allow about two minutes for the first start).
- `GET /status/<MEM_STATUS_SECRET>` → database status, model, record counts per namespace and state, strength
  quartiles, the last light and deep sleep with status, the number of open review items. No texts.
- `docker compose logs api worker` — the API logs `mem started: model=... namespaces=[...]` at start; the worker logs
  the next run times. Record texts and quotes are never logged.

## Backups

Back up the database with `pg_dump` on a schedule and keep copies off the host, encrypted:

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U mem -d mem -Fc > backups/mem-$(date +%F).dump
```

Restore into the same deployment:

```bash
docker compose stop api worker
docker compose exec -T postgres psql -U mem -d postgres -c "DROP DATABASE mem" -c "CREATE DATABASE mem OWNER mem"
docker compose exec -T postgres pg_restore -U mem -d mem < backups/mem-2026-10-09.dump
docker compose up -d api worker
```

Restoring on a new host: start only `postgres` first and create the service role, which `pg_dump` does not include
(it is a cluster-wide role), then restore as above:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U mem -d mem -c "CREATE ROLE mem_app NOLOGIN NOSUPERUSER NOBYPASSRLS"
```

Use the same `MEM_EMBED_MODEL` and `MEM_EMBED_DIM` as the original deployment, or the API will refuse to start. Test a
restore before you need one.

## Upgrading

```bash
docker compose exec -T postgres pg_dump -U mem -d mem -Fc > backups/mem-pre-upgrade-$(date +%F).dump
docker tag mem-api:latest mem-api:pre-upgrade      # keep the old image for a rollback
git pull
docker compose build api                           # the worker uses the same image
docker compose up -d api worker
docker compose logs -f api                         # look for "migration applied: ..." and "mem started"
```

Migrations are SQL files in `migrations/` named `NNNN_*.sql`. The API applies the missing ones at start, in order,
each in its own transaction, under a database lock, and records each by **file name** in `schema_migrations`. Files are
never edited after release; a change is always a new file. The API refuses to start if any migration is missing or if
the embedding model or dimension in the database differs from the configuration. The worker does not run migrations,
so start the API first (Compose does).

To roll back: stop `api` and `worker`, restore the pre-upgrade dump, `docker tag mem-api:pre-upgrade mem-api:latest`,
and start them again. A newer migration cannot be undone in place.

## Checklist

- `.env` is not in git; `MEM_DB_PASSWORD` and `MEM_ADMIN_TOKEN` are random.
- Port 3120 is bound to `127.0.0.1`; only the proxy is public, and it sets `X-Real-IP`.
- `MEM_STAND` is unset in production.
- Backups run and a restore has been tested.
- Alerts reach someone; the deep-sleep model is configured, or its stages are disabled.
