# 02. Модель данных

[English](../../design/02-data-model.md) · **Русский**

## Пространства, виды, слоты

**Пространство (`namespace`)** — изолированный набор записей: `work`, `assistant`. Новые — только через админ-операцию. Чтение чужого пространства — только по гранту (токену с этим пространством).

**Вид записи (`kind`)** — задаётся клиентом и **никогда не меняется сервисом** (P1). Вид задаёт параметры силы (период полураспада, порог забывания), защиту от забывания и вес в ранжировании. Список видов — таблица `kinds`, меняется миграцией.

| kind | Пространство | Что это | Полураспад силы | Забывается? |
|---|---|---|---|---|
| `decision` | work | Принятое решение | — (не затухает) | нет, только замещение |
| `convention` | work | Конвенция кода/проекта | — | нет, только замещение |
| `gotcha` | work | Грабля, обход | 180 дней | нет; при силе < 0,25 — в уборку «проверить актуальность» |
| `note` | work | Прочее | 90 дней | да |
| `person_fact` | assistant | Факт о человеке | 365 дней | да, если не подкрепляется |
| `topic_fact` | assistant | Контекст чата/темы/проекта | 180 дней | да |
| `episode` | assistant | Итог дня по чату | 30 дней | да (после сводки глубокого сна — быстрее, см. 05) |
| `commitment_mirror` | assistant | Зеркало обязательства из реестра агента | — пока открыто; 14 дней после `close` | да, только после закрытия |
| `insight` | оба | Вывод (клиента или принятая сводка сна) | 120 дней | да, если не подкрепляется |
| `feedback` | assistant | Правка владельца к работе агента | 180 дней | да |

**Слот (`slot`)** — «о чём именно этот факт»: `subject` + атрибут, например `person:petrov#role`, `person:petrov#reply_time`, `meeting:2026-09-25-chizhov#time`, `project:shop#deploy_process`. В одном слоте одновременно может быть **одна текущая** запись. Новая запись в занятый слот — это претендент на замещение (05). Слот задаёт клиент (агент выделяет атрибут при извлечении, Claude Code — по смыслу); глубокий сон может **предложить** слот для записей без него, но не назначает сам.

## Таблицы

```text
namespaces(id text pk, description, created_at)

kinds(id text pk, description,
      rank_weight real,                 -- вес вида в ранжировании (04)
      half_life_days int null,          -- null = не затухает
      forgettable bool,                 -- участвует ли в забывании по силе
      review_below real null)           -- порог силы для пометки «проверить» (gotcha)

memories(
  id            uuid pk,
  namespace     text fk,
  kind          text fk,
  text          text not null,
  subject       text null,              -- 'person:petrov', 'chat:dacha/смета', 'project:shop'
  slot          text null,              -- 'person:petrov#role'
  tags          text[] not null default '{}',
  importance    smallint not null default 2 check (importance between 1 and 3),  -- оценка клиента при записи
  source        text not null,          -- 'claude-code', 'cowork', 'assistant', 'import:legacy', 'sleep:deep'
  visibility    text not null default 'shareable'
                check (visibility in ('owner_only','same_chat','shareable')),     -- граница раскрытия (04)
  occurred_at   timestamptz null,       -- когда факт стал верным / событие произошло
  created_at    timestamptz not null default now(),

  -- жизненный цикл
  state         text not null default 'current'
                check (state in ('current','candidate','superseded','archived')),
  valid_from    timestamptz not null default now(),
  valid_to      timestamptz null,
  supersedes_id uuid null fk memories,
  duplicate_of  uuid null fk memories,
  pinned        bool not null default false,     -- владелец запретил забывание
  archived_at   timestamptz null,
  archived_by   text null,              -- 'rule:<id>' | 'strength' | 'client:<name>'

  -- сила (05)
  strength      real not null,          -- значение на момент strength_at
  strength_at   timestamptz not null,
  evidence_count int not null default 1,
  last_reinforced_at timestamptz not null,

  embedding     vector(<dim>) not null,
  fts           tsvector generated always as (to_tsvector('russian', text) || to_tsvector('english', text)) stored,
  external_ref  text null,
  external_closed_at timestamptz null
)
индексы: (namespace, state), GIN(tags), GIN(fts), HNSW(embedding), (namespace, subject),
         (namespace, kind), UNIQUE (namespace, slot) WHERE state = 'current'

evidence(                               -- доказательства: откуда известен факт
  id bigserial pk,
  memory_id uuid fk memories on delete cascade,
  source_ref text not null,             -- 'tg:<chat_id>/<msg_id>', 'meeting:<id>', 'claude-code:<session>', 'owner'
  source_scope text not null,           -- 'private:<chat>', 'group:<chat>', 'owner', 'dev'
  speaker text null,                    -- 'person:petrov' | 'owner' | 'agent'
  quote text null check (length(quote) <= 200),   -- короткая дословная цитата или null
  observed_at timestamptz not null,
  independent_key text not null,        -- по нему считается независимость: speaker + источник
  created_at timestamptz default now()
)

memory_events(                          -- события, меняющие силу (05)
  id bigserial pk, memory_id uuid fk on delete cascade, at timestamptz,
  type text check (type in ('evidence','owner_confirm','used','helpful','recalled_top5','contradicted','summarized_into')),
  delta real,                           -- фактическое изменение силы
  actor text)

review_items(                           -- очередь «расхождения и протухшее» для человека
  id bigserial pk, namespace, created_at,
  type text check (type in ('contradiction','stale','slot_suggestion','merge_suggestion')),
  memory_ids uuid[], proposal jsonb,    -- что предлагается сделать
  evidence_quotes jsonb,                -- цитаты, на которых основано
  status text check (status in ('open','accepted','rejected','expired')),
  decided_at, decided_by)

retention_rules(id text pk, namespace, kind, condition, action, after_days, enabled)   -- 05

audit_log(id bigserial, at, actor, action, namespace, memory_id null, rule_id null, detail jsonb)
   -- action: remember | add_evidence | supersede | revert_supersede | archive | unarchive | purge
   --         | mark_duplicate | reinforce | job_run | review_decision
   -- detail никогда не содержит текст записи или цитату

jobs(id, started_at, finished_at, kind check in ('light_sleep','deep_sleep','maintenance'),
     status check in ('RUNNING','OK','PARTIAL','FAILED'), stages jsonb, dry_run bool)

clients(id text pk, token_hash, namespaces text[], rights text[], created_at, last_seen_at)
```

## Инварианты [DET]

| ID | Инвариант | Как обеспечен |
|---|---|---|
| D-1 | `kind`, `text`, `tags`, `importance`, `slot` записи не изменяются после создания (слот может быть задан позже только принятием `slot_suggestion` из очереди) | Нет UPDATE-операций для этих полей в API; изменение = новая запись с `supersedes` |
| D-2 | В слоте не более одной записи в состоянии `current` | Частичный UNIQUE-индекс |
| D-3 | У `superseded` записи заполнены `valid_to` и есть ровно один преемник | Одна транзакция + UNIQUE(supersedes_id) |
| D-4 | `embedding` всегда заполнен; размерность = модели из конфигурации, иначе сервис не стартует | NOT NULL + проверка при старте (урок прежнего сервиса: схема разошлась с миграциями) |
| D-5 | У каждой записи есть хотя бы одно доказательство (`evidence`) | Запись и первое доказательство создаются в одной транзакции |
| D-6 | `strength` ∈ [0, 1]; пересчёт затухания не повышает силу | CHECK + property-тест (урок прежнего сервиса: пол 0,35 **поднимал** всё) |
| D-7 | После purge нет строк записи, её доказательств и событий | Физический DELETE с каскадом, тест |
| D-8 | Цитаты не попадают в `audit_log` и логи приложения | Тест на отсутствие текста в логах |

## Критерии приёмки

- Две записи в состоянии `current` в одном слоте → ошибка ограничения.
- Попытка создать запись без доказательства → транзакция откатывается.
- Старт с неверной размерностью вектора → процесс завершается с понятной ошибкой.
- После purge `SELECT count(*)` по записи в `memories`, `evidence`, `memory_events` = 0.
