# 02. Data model

**English** · [Русский](../ru/design/02-data-model.md)

## Namespaces, kinds, slots

**A namespace (`namespace`)** is an isolated set of records: `work`, `assistant`. New ones are created only by an
admin operation. Another namespace can be read only with a grant (a token that lists that namespace).

**A record kind (`kind`)** is set by the client and **never changed by the service** (P1). The kind sets the strength
parameters (half-life, forgetting threshold), protection from forgetting and the weight in ranking. The list of kinds is
the `kinds` table and changes by migration.

| kind | Namespace | What it is | Strength half-life | Forgotten? |
|---|---|---|---|---|
| `decision` | work | A decision taken | — (no decay) | no, only superseded |
| `convention` | work | A code or project convention | — | no, only superseded |
| `gotcha` | work | A pitfall and its workaround | 180 days | no; below strength 0.25 it goes to the clean-up as "check if still relevant" |
| `note` | work | Anything else | 90 days | yes |
| `person_fact` | assistant | A fact about a person | 365 days | yes, unless reinforced |
| `topic_fact` | assistant | Context of a chat, topic or project | 180 days | yes |
| `episode` | assistant | A day's summary of a chat | 30 days | yes (faster after a deep-sleep summary, see 05) |
| `commitment_mirror` | assistant | Mirror of a commitment from the agent's register | — while open; 14 days after `close` | yes, only after closing |
| `insight` | both | A conclusion (the client's, or an accepted sleep summary) | 120 days | yes, unless reinforced |
| `feedback` | assistant | The owner's correction to the agent's work | 180 days | yes |

**A slot (`slot`)** is "what exactly this fact is about": `subject` + attribute, for example `person:petrov#role`,
`person:petrov#reply_time`, `meeting:2026-09-25-chizhov#time`, `project:shop#deploy_process`. A slot can hold **one
current** record at a time. A new record in an occupied slot is a candidate for supersession (05). The client sets the
slot (the agent picks out the attribute during extraction, Claude Code by meaning); the deep sleep may **propose** a
slot for records without one, but never assigns it on its own.

## Tables

```text
namespaces(id text pk, description, created_at)

kinds(id text pk, description,
      rank_weight real,                 -- kind weight in ranking (04)
      half_life_days int null,          -- null = no decay
      forgettable bool,                 -- whether it takes part in forgetting by strength
      review_below real null)           -- strength threshold for a "check" mark (gotcha)

memories(
  id            uuid pk,
  namespace     text fk,
  kind          text fk,
  text          text not null,
  subject       text null,              -- 'person:petrov', 'chat:dacha/estimate', 'project:shop'
  slot          text null,              -- 'person:petrov#role'
  tags          text[] not null default '{}',
  importance    smallint not null default 2 check (importance between 1 and 3),  -- the client's estimate on write
  source        text not null,          -- 'claude-code', 'cowork', 'assistant', 'import:legacy', 'sleep:deep'
  visibility    text not null default 'shareable'
                check (visibility in ('owner_only','same_chat','shareable')),     -- disclosure boundary (04)
  occurred_at   timestamptz null,       -- when the fact became true / the event happened
  created_at    timestamptz not null default now(),

  -- life cycle
  state         text not null default 'current'
                check (state in ('current','candidate','superseded','archived')),
  valid_from    timestamptz not null default now(),
  valid_to      timestamptz null,
  supersedes_id uuid null fk memories,
  duplicate_of  uuid null fk memories,
  pinned        bool not null default false,     -- the owner forbade forgetting
  archived_at   timestamptz null,
  archived_by   text null,              -- 'rule:<id>' | 'strength' | 'client:<name>'

  -- strength (05)
  strength      real not null,          -- value at strength_at
  strength_at   timestamptz not null,
  evidence_count int not null default 1,
  last_reinforced_at timestamptz not null,

  embedding     vector(<dim>) not null,
  fts           tsvector generated always as (to_tsvector('russian', text) || to_tsvector('english', text)) stored,
  external_ref  text null,
  external_closed_at timestamptz null
)
indexes: (namespace, state), GIN(tags), GIN(fts), HNSW(embedding), (namespace, subject),
         (namespace, kind), UNIQUE (namespace, slot) WHERE state = 'current'

evidence(                               -- evidence: where a fact is known from
  id bigserial pk,
  memory_id uuid fk memories on delete cascade,
  source_ref text not null,             -- 'tg:<chat_id>/<msg_id>', 'meeting:<id>', 'claude-code:<session>', 'owner'
  source_scope text not null,           -- 'private:<chat>', 'group:<chat>', 'owner', 'dev'
  speaker text null,                    -- 'person:petrov' | 'owner' | 'agent'
  quote text null check (length(quote) <= 200),   -- a short verbatim quote or null
  observed_at timestamptz not null,
  independent_key text not null,        -- independence is computed from it: speaker + source
  created_at timestamptz default now()
)

memory_events(                          -- events that change strength (05)
  id bigserial pk, memory_id uuid fk on delete cascade, at timestamptz,
  type text check (type in ('evidence','owner_confirm','used','helpful','recalled_top5','contradicted','summarized_into')),
  delta real,                           -- the actual change of strength
  actor text)

review_items(                           -- the "disputed and stale" queue for a human
  id bigserial pk, namespace, created_at,
  type text check (type in ('contradiction','stale','slot_suggestion','merge_suggestion')),
  memory_ids uuid[], proposal jsonb,    -- what is proposed
  evidence_quotes jsonb,                -- the quotes it is based on
  status text check (status in ('open','accepted','rejected','expired')),
  decided_at, decided_by)

retention_rules(id text pk, namespace, kind, condition, action, after_days, enabled)   -- 05

audit_log(id bigserial, at, actor, action, namespace, memory_id null, rule_id null, detail jsonb)
   -- action: remember | add_evidence | supersede | revert_supersede | archive | unarchive | purge
   --         | mark_duplicate | reinforce | job_run | review_decision
   -- detail never contains a record's text or a quote

jobs(id, started_at, finished_at, kind check in ('light_sleep','deep_sleep','maintenance'),
     status check in ('RUNNING','OK','PARTIAL','FAILED'), stages jsonb, dry_run bool)

clients(id text pk, token_hash, namespaces text[], rights text[], created_at, last_seen_at)
```

## Invariants [DET]

| ID | Invariant | How it is enforced |
|---|---|---|
| D-1 | A record's `kind`, `text`, `tags`, `importance` and `slot` do not change after creation (a slot can be set later only by accepting a `slot_suggestion` from the queue) | No UPDATE operations for these fields in the API; a change = a new record with `supersedes` |
| D-2 | A slot has at most one record in state `current` | A partial UNIQUE index |
| D-3 | A `superseded` record has `valid_to` set and exactly one successor | One transaction + UNIQUE(supersedes_id) |
| D-4 | `embedding` is always set; its dimension = the configured model, otherwise the service does not start | NOT NULL + a check at start (lesson from the previous service: the schema drifted from the migrations) |
| D-5 | Every record has at least one piece of evidence (`evidence`) | The record and its first evidence are created in one transaction |
| D-6 | `strength` ∈ [0, 1]; recomputing decay never raises strength | CHECK + a property test (lesson from the previous service: a floor of 0.35 **raised** everything) |
| D-7 | After a purge there are no rows of the record, its evidence or its events | A physical DELETE with cascade, a test |
| D-8 | Quotes never reach `audit_log` or application logs | A test that the logs contain no text |

## Acceptance criteria

- Two records in state `current` in one slot → a constraint error.
- Creating a record without evidence → the transaction is rolled back.
- Starting with a wrong vector dimension → the process exits with a clear error.
- After a purge, `SELECT count(*)` for the record in `memories`, `evidence`, `memory_events` = 0.
