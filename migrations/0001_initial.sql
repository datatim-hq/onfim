-- mem: initial schema (spec 02). {{EMBED_DIM}} is filled in by the migration runner from the configuration.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE meta (
  key   text PRIMARY KEY,
  value text NOT NULL
);

CREATE TABLE namespaces (
  id          text PRIMARY KEY,
  description text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kinds (
  id             text PRIMARY KEY,
  description    text NOT NULL,
  namespaces     text[] NOT NULL,             -- namespaces where the kind is allowed
  rank_weight    real NOT NULL,
  half_life_days int NULL,                    -- NULL = does not decay
  forgettable    bool NOT NULL,
  review_below   real NULL
);

CREATE TABLE memories (
  id            uuid PRIMARY KEY,
  namespace     text NOT NULL REFERENCES namespaces(id),
  kind          text NOT NULL REFERENCES kinds(id),
  text          text NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
  subject       text NULL,
  slot          text NULL,
  tags          text[] NOT NULL DEFAULT '{}',
  importance    smallint NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),
  source        text NOT NULL,
  visibility    text NOT NULL DEFAULT 'shareable'
                CHECK (visibility IN ('owner_only','same_chat','shareable')),
  occurred_at   timestamptz NULL,
  created_at    timestamptz NOT NULL,

  state         text NOT NULL DEFAULT 'current'
                CHECK (state IN ('current','candidate','superseded','archived')),
  valid_from    timestamptz NOT NULL,
  valid_to      timestamptz NULL,
  supersedes_id uuid NULL REFERENCES memories(id) ON DELETE SET NULL,
  duplicate_of  uuid NULL REFERENCES memories(id) ON DELETE SET NULL,
  pinned        bool NOT NULL DEFAULT false,
  archived_at   timestamptz NULL,
  archived_by   text NULL,
  prev_state    text NULL,                    -- state before archiving (for unarchive)

  strength      real NOT NULL CHECK (strength >= 0 AND strength <= 1),
  strength_at   timestamptz NOT NULL,
  evidence_count int NOT NULL DEFAULT 1,
  last_reinforced_at timestamptz NOT NULL,

  embedding     vector({{EMBED_DIM}}) NOT NULL,
  fts           tsvector GENERATED ALWAYS AS
                (to_tsvector('russian', text) || to_tsvector('english', text)) STORED,
  external_ref  text NULL,
  external_closed_at timestamptz NULL
);

CREATE INDEX memories_ns_state   ON memories (namespace, state);
CREATE INDEX memories_tags       ON memories USING gin (tags);
CREATE INDEX memories_fts        ON memories USING gin (fts);
CREATE INDEX memories_embedding  ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_ns_subject ON memories (namespace, subject);
CREATE INDEX memories_ns_kind    ON memories (namespace, kind);
CREATE UNIQUE INDEX memories_slot_current ON memories (namespace, slot) WHERE state = 'current';   -- D-2
CREATE UNIQUE INDEX memories_supersedes   ON memories (supersedes_id) WHERE supersedes_id IS NOT NULL; -- D-3
CREATE UNIQUE INDEX memories_external_ref ON memories (namespace, external_ref) WHERE external_ref IS NOT NULL; -- A-3

CREATE TABLE evidence (
  id              bigserial PRIMARY KEY,
  memory_id       uuid NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  source_ref      text NOT NULL,
  source_scope    text NOT NULL,
  speaker         text NULL,
  quote           text NULL CHECK (quote IS NULL OR length(quote) <= 200),
  observed_at     timestamptz NOT NULL,
  independent_key text NOT NULL,
  created_at      timestamptz NOT NULL
);
CREATE INDEX evidence_memory ON evidence (memory_id);
CREATE UNIQUE INDEX evidence_memory_ref ON evidence (memory_id, source_ref);

CREATE TABLE memory_events (
  id        bigserial PRIMARY KEY,
  memory_id uuid NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  at        timestamptz NOT NULL,
  type      text NOT NULL CHECK (type IN ('evidence','owner_confirm','used','helpful',
                                          'recalled_top5','contradicted','summarized_into')),
  delta     real NOT NULL,
  actor     text NOT NULL
);
CREATE INDEX memory_events_memory ON memory_events (memory_id, at);

CREATE TABLE review_items (
  id              bigserial PRIMARY KEY,
  namespace       text NOT NULL REFERENCES namespaces(id),
  created_at      timestamptz NOT NULL,
  type            text NOT NULL CHECK (type IN ('contradiction','stale','slot_suggestion','merge_suggestion')),
  memory_ids      uuid[] NOT NULL,
  proposal        jsonb NOT NULL DEFAULT '{}',
  evidence_quotes jsonb NOT NULL DEFAULT '[]',
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','rejected','expired')),
  decided_at      timestamptz NULL,
  decided_by      text NULL
);
CREATE INDEX review_items_ns_status ON review_items (namespace, status);

CREATE TABLE retention_rules (
  id         text PRIMARY KEY,
  namespace  text NOT NULL,          -- '*' = all
  kind       text NOT NULL,          -- '*' = all
  condition  text NOT NULL CHECK (condition IN ('archived_older_than','superseded_older_than')),
  action     text NOT NULL CHECK (action IN ('archive','purge')),
  after_days int NOT NULL,
  enabled    bool NOT NULL DEFAULT true
);

CREATE TABLE audit_log (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL,
  actor     text NOT NULL,
  action    text NOT NULL CHECK (action IN ('remember','add_evidence','supersede','revert_supersede',
                                            'archive','unarchive','purge','mark_duplicate','reinforce',
                                            'job_run','review_decision','pin','unpin','close','promote')),
  namespace text NULL,
  memory_id uuid NULL,               -- no FK: the purge entry outlives the deleted record
  rule_id   text NULL,
  detail    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_memory ON audit_log (memory_id);

CREATE TABLE jobs (
  id          bigserial PRIMARY KEY,
  started_at  timestamptz NOT NULL,
  finished_at timestamptz NULL,
  kind        text NOT NULL CHECK (kind IN ('light_sleep','deep_sleep','maintenance')),
  status      text NOT NULL CHECK (status IN ('RUNNING','OK','PARTIAL','FAILED')),
  stages      jsonb NOT NULL DEFAULT '{}',
  dry_run     bool NOT NULL DEFAULT false
);

CREATE TABLE clients (
  id           text PRIMARY KEY,
  token_hash   text NOT NULL UNIQUE,
  namespaces   text[] NOT NULL,
  rights       text[] NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NULL
);

-- Reference data (namespaces are created by `mem init`)
INSERT INTO kinds (id, description, namespaces, rank_weight, half_life_days, forgettable, review_below) VALUES
  ('decision',          'Принятое решение',                        '{work}',        1.0,  NULL, false, NULL),
  ('convention',        'Конвенция кода/проекта',                  '{work}',        0.9,  NULL, false, NULL),
  ('gotcha',            'Грабля, обходной путь',                   '{work}',        0.9,  180,  false, 0.25),
  ('note',              'Прочее',                                  '{work}',        0.5,  90,   true,  NULL),
  ('person_fact',       'Факт о человеке',                         '{assistant}',      0.9,  365,  true,  NULL),
  ('topic_fact',        'Контекст чата/темы/проекта',              '{assistant}',      0.8,  180,  true,  NULL),
  ('episode',           'Итог дня по чату',                        '{assistant}',      0.4,  30,   true,  NULL),
  ('commitment_mirror', 'Зеркало обязательства из реестра агента', '{assistant}',      1.0,  14,   true,  NULL),
  ('insight',           'Вывод (клиента или принятая сводка сна)', '{work,assistant}', 0.7,  120,  true,  NULL),
  ('feedback',          'Правка владельца к работе агента',        '{assistant}',      0.8,  180,  true,  NULL);

INSERT INTO retention_rules (id, namespace, kind, condition, action, after_days) VALUES
  ('r-assistant-purge',    'assistant', '*', 'archived_older_than',   'purge',   365),
  ('r-history-archive', '*',      '*', 'superseded_older_than', 'archive', 180);
