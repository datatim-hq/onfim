# 03. API: REST и MCP

[English](../../design/03-api.md) · **Русский**

## Назначение

Один набор операций для всех клиентов. REST — для агента и скриптов, MCP — для Cowork и Claude Code. Оба вызывают один сервисный слой (P7): фильтры, права и ошибки ведут себя одинаково.

## Авторизация

`Authorization: Bearer <token>` на всех запросах, кроме `/status/<секрет>`. Права токена: пространства × `read` / `write` / `review` / `forget` / `admin` (01, S-1).

## REST (`/v1/{namespace}/…`)

### Запись и чтение

| Метод и путь | Операция | Право |
|---|---|---|
| `POST /memories` | **remember**. Тело: `text`, `kind`, `subject?`, `slot?`, `tags?`, `importance? (1–3)`, `visibility?`, `occurred_at?`, `external_ref?`, `supersedes?`, **`evidence`** (обязательно, хотя бы одно: `source_ref`, `source_scope`, `speaker?`, `quote? (≤200)`, `observed_at`). Ответ: запись, её `state` (`current` / `candidate` / `superseded`) и, если слот занят, id текущей записи | write |
| `POST /memories/{id}/evidence` | добавить доказательство к существующей записи (подкрепление, 05 §1). Повтор с тем же `source_ref` игнорируется | write |
| `GET /memories/{id}` | запись + все доказательства + последние события силы | read |
| `GET /memories` | **list**: фильтры `kind`, `subject`, `slot`, `tags`, `tags_mode`, `state`, `created_after/before`, `as_of`, `audience`, `min_strength`, `limit`, `cursor`, сортировка `strength` \| `created_at` | read |
| `POST /recall` | **recall**: `query`, те же фильтры, `include_candidates?`, `limit (≤50)`, `explain?` → записи с силой, доказательствами, признаком спора (04) | read |
| `GET /memories/{id}/history` | цепочка замещений и претендентов по слоту с `valid_from/valid_to` и основаниями (G1a/b/c, явное, откат) | read |

### Сигналы (подкрепление)

| Метод и путь | Событие | Право |
|---|---|---|
| `POST /memories/{id}/used` | запись использована в ответе/брифинге | write |
| `POST /memories/{id}/helpful` | запись оказалась полезной | write |
| `POST /memories/{id}/confirm` | подтверждение владельцем (`owner_confirm`) — только токены с правом `review` | review |
| `POST /memories/{id}/pin`, `/unpin` | запрет забывания | review |
| `POST /memories/{id}/close` | для `commitment_mirror`: внешний объект закрыт | write |

### Замещение, архив, удаление

| Метод и путь | Операция | Право |
|---|---|---|
| `POST /memories/{id}/revert-supersede` | откат замещения в пределах 30 дней | review |
| `POST /memories/{id}/archive`, `/unarchive` | ручная архивация/возврат | write |
| `DELETE /memories/{id}?mode=purge` | физическое удаление одной записи со всеми доказательствами и событиями | forget |
| `POST /forget` | массово: фильтр (`subject`/`slot`/`tags`/`kind`, хотя бы один обязателен) + `mode=archive\|purge` + `dry_run` (по умолчанию `true`); без `dry_run=false` и `confirm=<N>`, равного числу подпадающих записей, ничего не делает | forget |

### Уборка и очередь решений

| Метод и путь | Операция | Право |
|---|---|---|
| `GET /sleep/latest?kind=light\|deep` | отчёт последнего сна (05, §3) с цитатами | read |
| `GET /sleep/{job_id}` | отчёт конкретного прогона | read |
| `GET /review?status=open` | очередь: расхождения, «протухло?», предложения слотов и сводок — с цитатами | review |
| `POST /review/{id}/accept` \| `/reject` | решение по пункту; `accept` для `contradiction` требует указать, какая запись верна (`winner`) | review |
| `POST /sleep/run` | ручной запуск `light` или `deep`, `dry_run` по умолчанию `true` | admin |

### Служебные

`GET /stats` (счётчики по видам, состояниям, распределение силы), `GET /export`, `POST /import` (admin, `dry_run` по умолчанию), `GET /status/<секрет>`.

### Ошибки

`400` — невалидный ввод (неизвестный `kind`, нет `evidence`, `supersedes` на чужое пространство или на не-текущую запись), `401`, `403`, `404`, `409` — конфликт в слоте при явном `supersedes` не той записи, `503` — модель эмбеддингов недоступна (запись не создана, F-11). Тело: `{"error": "<код>", "message": "<текст>"}`.

## MCP

Endpoint: `https://mem.example.com/mcp/{namespace}/` (streamable HTTP). Пространство — из пути, токен — из заголовка.

| Инструмент | = REST | Замечание |
|---|---|---|
| `remember` | POST /memories | `evidence` для Claude Code — `source_ref=claude-code:<проект>`, `speaker=agent`; для слов владельца в диалоге — `speaker=owner` |
| `add_evidence` | POST …/evidence | «это подтвердилось ещё раз» |
| `recall` | POST /recall | `tags` фильтруют реально (P7) |
| `get`, `list`, `history` | GET … | |
| `used`, `helpful` | POST …/used, …/helpful | Claude Code вызывает `used`, когда запись помогла в работе |
| `confirm`, `pin` | POST …/confirm, …/pin | только токены с `review` |
| `review_list`, `review_decide` | GET /review, POST /review/{id}/… | разбор уборки прямо из Cowork |
| `sleep_report` | GET /sleep/latest | |
| `forget` | POST /forget | только токены с `forget`; всегда сначала `dry_run` |
| `stats` | GET /stats | |

Описания инструментов перечисляют допустимые `kind` и примеры слотов для пространства (по умолчанию на английском, на русском — при `MEM_MCP_LANG=ru`).

## Функциональные требования

| ID | Требование | Соавт. | Тип |
|---|---|---|---|
| A-1 | REST и MCP — тонкие адаптеры над `mem.service`; бизнес-логики в адаптерах нет | 4 | DET |
| A-2 | OpenAPI-схема хранится в репозитории; изменение без обновления файла валит CI | 4 | DET |
| A-3 | `remember` идемпотентен по `(namespace, external_ref)`; повтор с новым доказательством = `add_evidence` | 4 | DET |
| A-4 | Лимит `text` — 4 000 символов, `quote` — 200 | 4 | DET |
| A-5 | Право `review` есть только у токенов владельца (бот агента действует от имени владельца после нажатия кнопки; ассистент разработки — владелец) | 4 | DET |

## Критерии приёмки

- Для каждого MCP-инструмента тест: тот же результат, что у REST-эквивалента на тех же данных.
- `remember` без `evidence` → 400, записи нет.
- `recall` с `tags=[project:shop]` через MCP не возвращает записей без тега.
- `POST /forget` без `dry_run=false` ничего не меняет; с неверным `confirm` → 400.
- Токен без `review` на `POST /confirm` → 403.
