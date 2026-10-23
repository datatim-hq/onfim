# REST API

[English](../rest-api.md) · **Русский**

Все операции — под `/v1/`. Те же операции есть в MCP ([mcp.md](mcp.md)); оба интерфейса вызывают один сервисный слой,
поэтому фильтры, права и ошибки ведут себя одинаково. Понятия, которые встречаются ниже (виды, доказательства, слоты,
сила, очередь решений), объяснены в [concepts.md](concepts.md).

В примерах — адрес `http://127.0.0.1:3120` (его публикует Docker Compose) и пространство `alice.work`, созданное
командой `python -m mem.cli init --tenant acme --person alice`.

```bash
BASE=http://127.0.0.1:3120
AUTH="Authorization: Bearer <token>"
JSON="Content-Type: application/json"
```

## Аутентификация

Каждому запросу к `/v1/` нужен заголовок `Authorization: Bearer <token>`. Токены один раз печатают `mem.cli init`
или `mem.cli create-client`; в базе хранится только их хеш. Нет токена, токен неизвестен или его участник
деактивирован — `401`.

Токен принадлежит участнику: человеку, агенту, действующему от имени человека, или сервису. Запрос может сделать
пересечение двух вещей:

- **гранты** человека (или группы) в пространстве, которые дают роль: `reader` → `read`; `contributor` → `read`,
  `write`; `curator` → `+review`, `forget`; `owner` → `+manage`;
- **потолок** токена: пространства и права, записанные в сам токен.

Пространство, к которому нет доступа, отвечает `403` — неважно, существует оно или нет. Запись выше вашего уровня
классификации отвечает `404`.

**Делегированные клиенты** (токены с `--delegate-group`) в каждом запросе передают `X-Mem-On-Behalf-Of: <person>` или
`X-Mem-On-Behalf-Of: group:<group>`; без заголовка ответ — `400`. Любой другой токен с этим заголовком тоже получает
`400`.

<a id="local-admin"></a>

### Локальный администратор

Право `admin` действует только для **прямых** запросов к сервису. Запрос с заголовком `X-Real-IP` или
`X-Forwarded-For` считается внешним и не получает прав администратора, даже с токеном администратора: встроенный
токен `admin` тогда не имеет доступа ни к одному пространству. Локально токен с правом `admin` имеет все права во всех
пространствах всех организаций, до классификации 3.

Поэтому обратный прокси должен всегда ставить один из этих заголовков (см.
[operations.md](operations.md#running-behind-a-tls-reverse-proxy)). Токен администратора задаётся в
`MEM_ADMIN_TOKEN`; CLI работает с базой напрямую, и токен ему не нужен.

Маршруты только для локального администратора: `POST /v1/_admin/sleep/run`, `POST /v1/_stand/clock`,
`POST /v1/{ns}/import`. `GET /v1/{ns}/export` доступен локальному администратору или владельцу с правом `manage`.

## Ошибки

Ошибки приходят в JSON:

```json
{"error": "forbidden", "message": "..."}
```

| Код | `error` | Когда |
|---|---|---|
| 400 | `bad_request` | неверный ввод: нет `text`/`kind`/`evidence`, неизвестный вид, вид не разрешён профилем пространства, текст длиннее 4000 или цитата длиннее 200 символов, секрет в тексте, классификация выше вашего допуска, неверное тело или параметр запроса |
| 401 | `unauthorized` | нет токена или токен неизвестен |
| 403 | `forbidden` | нет доступа к пространству или нет нужного права |
| 404 | `not_found` | нет записи, пункта очереди или отчёта сна (в том числе для записей выше вашей классификации) |
| 409 | `conflict` | конфликт слота при явном `supersedes`; пункт очереди уже решён; перенос уже ждёт решения |
| 503 | `embedder_unavailable` | модель эмбеддингов не ответила; ничего не записано |

Опирайтесь на `error`; `message` — текст для человека на английском, он может меняться от версии к версии. На
неизвестные пути FastAPI отвечает по умолчанию: `404 {"detail": "Not Found"}`.

## Служебные маршруты (без токена)

### `GET /healthz`

Проверка, что сервис жив. Возвращает `{"ok": true}`.

### `GET /status/{secret}`

Счётчики для мониторинга, только если задан `MEM_STATUS_SECRET` и `{secret}` с ним совпадает; иначе `404`. Возвращает
`dbOk`, `embedDim`, `modelId`, `time`, `clockOffsetS`, число записей по пространствам и состояниям (`memories`),
квартили силы текущих записей (`strengthQuartiles`), `lastLightSleep` и `lastDeepSleep` (`{at, status}`) и
`reviewOpen`. Текстов нет.

## Записи

### Объект записи

При чтении запись выглядит так (`get` добавляет все `evidence` и 20 последних событий силы `events`; `list` и `recall`
добавляют до трёх последних доказательств):

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

`strength` — текущее значение с учётом затухания на момент запроса.

### `POST /v1/{ns}/memories` — запомнить

Право: `write`. Возвращает `201` и запись.

| Поле | Обязательно | Примечание |
|---|---|---|
| `text` | да | непустой, не длиннее 4000 символов; скрытые символы вырезаются, секреты отклоняются |
| `kind` | да | должен быть разрешён профилем пространства |
| `evidence` | да | список доказательств (или одно доказательство объектом); поля ниже |
| `subject` | нет | |
| `slot` | нет | новая запись в занятом слоте становится претендентом (`candidate`) |
| `tags` | нет | список строк |
| `importance` | нет | 1, 2 (по умолчанию), 3 |
| `visibility` | нет | `shareable` (по умолчанию), `same_chat`, `owner_only` |
| `classification` | нет | 0–3; по умолчанию — уровень пространства; выше вашего допуска → `400` |
| `occurred_at` | нет | ISO 8601 |
| `external_ref` | нет | ключ идемпотентности внутри пространства |
| `supersedes` | нет | id **текущей** записи, которую нужно явно заменить |
| `source` | нет | учитывается только у локального администратора (импорт); иначе `source` — это id клиента |

Поля доказательства: `source_ref`, `source_scope`, `observed_at` (обязательны); `speaker`, `quote` (не длиннее 200),
`speaker_principal` (участник организации пространства), `forwarded_from`, `independent_key` (необязательны).

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

Дополнительные поля в ответе, когда они применимы:

- `slot_current_id` — слот был занят, эта запись стала претендентом (значение `"hidden"`, если текущая запись выше
  вашего допуска);
- `idempotent: true` и `evidence_results` — запись с таким `external_ref` уже была, доказательство добавлено к ней;
- `reinforced` и `evidence_results` — такое же утверждение уже было текущим в слоте, доказательство добавлено к нему.

Правило (`decision`, `convention`, `gotcha`) из недоверенного доказательства или от не-куратора в пространстве команды
сохраняется претендентом с пунктом очереди `rule_confirm`.

### `GET /v1/{ns}/memories` — список

Право: `read`. Список записей без поиска. Параметры запроса:

| Параметр | Примечание |
|---|---|
| `kind`, `subject`, `slot` | точное совпадение; повторите параметр, чтобы подошло любое из значений. `subject` учитывает псевдонимы |
| `tags` | повтором или через запятую; `tags_mode=any` (по умолчанию) или `all` |
| `state` | `current` (по умолчанию), `candidate`, `superseded`, `archived` или `all`; можно повторять |
| `include_candidates`, `include_archived` | `true` добавляет эти состояния к `current` по умолчанию |
| `as_of` | время ISO: записи, действовавшие в тот момент |
| `created_after`, `created_before` | время ISO |
| `audience` | `chat:<id>`: только записи, которые можно произнести в этом чате |
| `min_strength` | число |
| `sort` | `created_at` (по умолчанию, сначала новые) или `strength` |
| `limit` | 1–200, по умолчанию 20 |
| `cursor` | `next_cursor` с предыдущей страницы |

Возвращает `{"items": [...], "next_cursor": "..." | null}`. Дубли, скрытые сном, в список не попадают.

```bash
curl -sS "$BASE/v1/alice.work/memories?tags=project:shop&kind=gotcha&sort=strength&limit=50" -H "$AUTH"
```

### `GET /v1/{ns}/memories/{id}` — запись

Право: `read`. Запись со всеми доказательствами и 20 последними событиями силы (`{at, type, delta, actor}`).

### `POST /v1/{ns}/memories/{id}/evidence` — добавить доказательство

Право: `write`. Тело: одно доказательство. Независимое доказательство подкрепляет на 0,30, повтор — на 0,05; повтор
того же `source_ref` игнорируется. Доверенное доказательство делает запись доверенной.

```bash
curl -sS -X POST "$BASE/v1/alice.work/memories/<id>/evidence" -H "$AUTH" -H "$JSON" -d '{
  "source_ref": "https://github.com/acme/shop/pull/12", "source_scope": "repo",
  "observed_at": "2026-10-10T09:00:00Z"}'
```

Возвращает `{"id", "changed": true, "independent", "strength_before", "strength"}` или
`{"id", "changed": false, "reason"}`.

### `GET /v1/{ns}/memories/{id}/history` — история

Право: `read`. Все версии в слоте записи (без слота — в её цепочке замен), от старых к новым, и переходы
(`supersede`, `promote`, `revert_supersede`, `archive`, `unarchive`) со временем, автором и причиной. Возвращает
`{"slot", "records": [...], "transitions": [...]}`.

### `POST /v1/{ns}/recall` — поиск

Право: `read`. Гибридный поиск (векторный и полнотекстовый) с формулой ранжирования из
[concepts.md](concepts.md#strength-and-decay).

| Поле | Примечание |
|---|---|
| `query` | вопрос своими словами; пустой → `list` с сортировкой по силе |
| `limit` | 1–50, по умолчанию 10 |
| `explain` | `true` добавляет к каждой записи `explain: {sem, lex, strength, prior, score}` |
| фильтры | те же, что у `list`: `tags`, `tags_mode`, `kind`, `subject`, `slot`, `state`, `include_candidates`, `include_archived`, `as_of`, `created_after`, `created_before`, `audience`, `min_strength` |

```bash
curl -sS -X POST "$BASE/v1/alice.work/recall" -H "$AUTH" -H "$JSON" \
  -d '{"query": "how do we deploy the shop?", "tags": ["project:shop"], "explain": true}'
```

Возвращает `{"items": [...], "query": "..."}`. У каждой записи есть `score` и `contested`; у оспоренной текущей записи
ещё `candidate_id`. Пять первых результатов получают небольшое подкрепление (не чаще раза в сутки на запись).

### `POST /v1/recall` — поиск по слоям

Право: `read` в каждом пространстве, где идёт поиск. Ищет во всех пространствах, которые вы можете читать, или в
заданном списке, и сливает результаты с весом слоя (личный 1,0, группа 0,95, организация 0,9).

| Поле | Примечание |
|---|---|
| `query`, `limit`, фильтры | как у `recall` |
| `namespaces` | список id пространств; любое, которое вы не можете читать, → `403` |
| `scopes` | подмножество `personal`, `group`, `tenant` (списком или через запятую) |

```bash
curl -sS -X POST "$BASE/v1/recall" -H "$AUTH" -H "$JSON" -d '{"query": "release process", "scopes": ["personal", "group"]}'
```

Возвращает `{"items": [...], "query", "namespaces": [<где искали>]}`; у каждой записи есть `layer: {namespace, scope}`.

## Сигналы

Все возвращают `{"id", "event", "strength_before", "strength"}` и не принимают тела.

| Маршрут | Право | Действие |
|---|---|---|
| `POST /v1/{ns}/memories/{id}/used` | `write` | запись использована в работе или ответе (+0,15) |
| `POST /v1/{ns}/memories/{id}/helpful` | `write` | запись оказалась полезной (+0,25) |
| `POST /v1/{ns}/memories/{id}/confirm` | `review` | подтверждение владельцем (+0,50); ещё добавляет доказательство от владельца и делает запись доверенной. Вызывать только по прямому слову человека |

## Управление записями

| Маршрут | Право | Действие |
|---|---|---|
| `POST /v1/{ns}/memories/{id}/pin` | `review` | запись не затухает и не забывается; возвращает запись |
| `POST /v1/{ns}/memories/{id}/unpin` | `review` | отменяет `pin` |
| `POST /v1/{ns}/memories/{id}/close` | `write` | только для `commitment_mirror` и `crm_mirror`: внешний объект закрыт, запись начинает затухать |
| `POST /v1/{ns}/memories/{id}/archive` | `write` | скрывает запись из поиска; обратимо |
| `POST /v1/{ns}/memories/{id}/unarchive` | `write` | возвращает запись в прежнее состояние с силой 0,30; бывшая текущая запись возвращается претендентом, если её слот уже занят |
| `POST /v1/{ns}/memories/{id}/revert-supersede` | `review` | в течение 30 дней после замены: старая запись снова текущая, эта — претендент. Возвращает `{"current", "candidate"}` |
| `DELETE /v1/{ns}/memories/{id}?mode=purge` | `forget` | удаляет запись, её доказательства, события и вектор. Возвращает `{"purged": 1, "id"}` |
| `DELETE /v1/{ns}/memories/{id}?mode=archive` | `write` | то же, что `archive` |

По умолчанию `mode` — `purge`.

### `POST /v1/{ns}/forget` — массовое забывание

Право: `forget`.

| Поле | Примечание |
|---|---|
| `subject`, `slot`, `kind`, `tags`, `source_ref` | нужно хотя бы одно; `tags` должны быть все; `source_ref` находит все записи, у которых этот источник среди доказательств, — так можно разом убрать всё, что взято из одного утёкшего сообщения |
| `mode` | `archive` (по умолчанию) или `purge` |
| `dry_run` | по умолчанию `true`: только посчитать |
| `confirm` | при `dry_run: false` должен совпадать с числом найденных записей |

```bash
# 1. посмотреть, что уйдёт
curl -sS -X POST "$BASE/v1/alice.work/forget" -H "$AUTH" -H "$JSON" -d '{"subject": "project:old", "mode": "purge"}'
# 2. применить, подтвердив число из шага 1
curl -sS -X POST "$BASE/v1/alice.work/forget" -H "$AUTH" -H "$JSON" \
  -d '{"subject": "project:old", "mode": "purge", "dry_run": false, "confirm": 7}'
```

Возвращает `{"mode", "matched", "dry_run", "sample": [до 10 {id, kind, subject}]}`, после применения ещё `applied`.
При `purge` удаляются и пункты очереди решений, где упомянута удалённая запись: в них её цитаты.

## Память команды

### `POST /v1/{ns}/memories/{id}/promote` — предложить запись в другой слой

Право: `write` в исходном и в целевом пространстве, оба в одной организации. Запись должна быть текущей.

| Поле | Примечание |
|---|---|
| `to` | целевое пространство |
| `share_quotes` | по умолчанию `true`; `false` не переносит цитаты в предложение |
| `note` | свободный текст для куратора |

Возвращает `{"status": "open", "to", "review_id"}` или `{"status": "accepted", "to", "memory_id", "state"}`, если
вид записи указан в настройке `promotion_auto` целевого пространства. Второе предложение, пока открыто первое, →
`409`.

### `POST /v1/{ns}/aliases` — псевдоним субъекта

Право: `write`. Тело `{"alias": "person:tg123", "canonical": "crm:contact:77"}`. Псевдоним действует на всю
организацию пространства; фильтры по `subject` учитывают его в обе стороны. Возвращает `{"alias", "canonical",
"tenant"}`.

## Очередь решений

### `GET /v1/{ns}/review?status=open`

Право: `review`. `status`: `open` (по умолчанию), `accepted`, `rejected`, `expired`, `all`. Возвращает
`{"items": [{"id", "type", "status", "created_at", "namespace", "proposed_by", "proposal", "records": [{"id", "kind",
"text", "slot", "state", "quote"}]}]}`. Пункт `promotion` несёт предлагаемую запись (текст, вид, доказательства,
классификацию) в `proposal`. Пункты с записями выше вашей классификации не показываются.

### `GET /v1/review?status=open`

То же по всем пространствам, где у вас есть `review`; у каждого пункта есть `layer: {namespace, scope}`.

### `POST /v1/{ns}/review/{id}/accept` и `POST /v1/{ns}/review/{id}/reject`

Право: `review`. Чтобы принять `contradiction`, нужно `{"winner": "<id верной записи>"}`. Что делает каждое решение
для каждого типа пункта — в [concepts.md](concepts.md#the-review-queue). Пункт, который уже не открыт, → `409`.

```bash
curl -sS -X POST "$BASE/v1/alice.work/review/12/accept" -H "$AUTH" -H "$JSON" -d '{"winner": "<id>"}'
```

## Сон

### `GET /v1/{ns}/sleep/latest?kind=light|deep`

Право: `read`. Отчёт последнего завершённого прогона этого вида (настоящие прогоны важнее пробных) по этому
пространству: `job_id`, `status`, `dry_run`, `started_at`, `finished_at`, `stages` (статусы и счётчики), `replaced`,
`forgotten`, `duplicates_merged`, `candidates_expired`, `contradictions_open`; у глубокого сна ещё
`slot_suggestions`, `stale`, `summaries_applied`, `summaries_rejected`, `model_items_skipped`. Если прогонов не было —
`404`.

### `GET /v1/{ns}/sleep/{job_id}`

Право: `read`. Отчёт одного прогона.

### `POST /v1/_admin/sleep/run`

Только локальный администратор. Запускает сон в процессе API и возвращает результат.

| Поле | Примечание |
|---|---|
| `kind` | `light` (по умолчанию) или `deep` |
| `dry_run` | по умолчанию `true`; `false` — применить |

Возвращает `{"job_id", "status", "dry_run", "stages"}` или `{"status": "SKIPPED", "reason"}`, если сон этого вида
уже идёт.

```bash
curl -sS -X POST "$BASE/v1/_admin/sleep/run" -H "Authorization: Bearer <admin-token>" -H "$JSON" \
  -d '{"kind": "light", "dry_run": true}'
```

## Обслуживание

### `GET /v1/{ns}/stats`

Право: `read`. `{"namespace", "counts": {kind: {state: n}}, "strength_current": {"n", "q1", "median", "q3", "avg"}}`.

### `GET /v1/{ns}/export`

Право: `manage` (роль владельца пространства и `manage` в токене) или локальный администратор. Отдаёт поток NDJSON
(`application/x-ndjson`): по записи в строке во всех состояниях, со всеми доказательствами и `stored_strength`.

```bash
curl -sS "$BASE/v1/alice.work/export" -H "Authorization: Bearer <admin-token>" > alice.work.jsonl
```

### `POST /v1/{ns}/import`

Только локальный администратор. Тело `{"records": [...], "dry_run": true}`; у каждой записи те же поля, что у
`remember` (её `source` сохраняется). При `dry_run` (по умолчанию) проверяются только поля и возвращается
`{"dry_run", "records", "problems"}`; если хоть у одной записи есть проблема, ничего не записывается. Повтор с тем же
`external_ref` добавляет доказательство, а не создаёт дубль.

### `POST /v1/_stand/clock`

Только локальный администратор и только при `MEM_STAND=1` (тестовый стенд). Сдвигает часы сервиса, чтобы затухание и
забывание проверялись за минуты. Тело: одно из `{"offset_s": N}`, `{"advance_s": N}`, `{"advance_days": N}`.
Возвращает `{"offset_s", "now"}`.

## Прочие пути

- `/mcp/{namespace}/` — адрес MCP ([mcp.md](mcp.md)).
- `/openapi.json` — схема, которую генерирует FastAPI. Тела запросов в ней без типов; справка — эта страница.
