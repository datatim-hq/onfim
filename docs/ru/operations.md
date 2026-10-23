# Эксплуатация

[English](../operations.md) · **Русский**

Как запустить Onfim в продакшене: настройки, CLI администратора, обратный прокси, исполнитель сна, оповещения, модель
глубокого сна, резервные копии и обновления.

## Что запускается

`docker compose up -d` запускает три контейнера (проект Compose `mem`):

| Сервис | Что делает | Лимит памяти |
|---|---|---|
| `postgres` | Postgres 16 с pgvector; на хост не публикуется | 512 МБ |
| `api` | REST и MCP на одном порту, публикуется как `127.0.0.1:3120`; считает эмбеддинги локально; при старте применяет миграции | 1536 МБ |
| `worker` | тот же образ с командой `python -m mem.jobs`: ночной и еженедельный сон | 1280 МБ |

Тома: `mem_pg` (база), `mem_models` (модель эмбеддингов; скачивается с Hugging Face при первом старте, поэтому первому
старту нужен выход в интернет и он занимает время), `mem_exports` (ночные выгрузки JSONL от исполнителя).
Контейнер `api` также подключён к сети Docker `mem_clients` под именем `mem-api`, поэтому другие контейнеры могут
обращаться к нему по `http://mem-api:3120`, минуя прокси; Postgres в эту сеть не входит.

## Переменные окружения

Задайте их в `.env` рядом с `docker-compose.yml` (за основу возьмите `.env.example`; `.env` никогда не коммитьте). Его
читают и `api`, и `worker`.

| Переменная | По умолчанию | Значение |
|---|---|---|
| `MEM_DB_PASSWORD` | — (обязательна) | пароль Postgres; из него Compose собирает `MEM_DATABASE_URL` |
| `MEM_DATABASE_URL` | `postgresql://mem:mem@localhost:5432/mem` | адрес базы; Compose задаёт его сам |
| `MEM_EMBED_MODEL` | `intfloat/multilingual-e5-small` | модель эмбеддингов (fastembed/ONNX, локально). Подходит и `intfloat/multilingual-e5-base` (768); `fake` — детерминированные эмбеддинги для тестов |
| `MEM_EMBED_DIM` | `384` | размерность вектора; должна совпадать с моделью |
| `MEM_HOST` | `0.0.0.0` | адрес внутри контейнера |
| `MEM_PORT` | `3120` | порт внутри контейнера (Compose публикует 3120) |
| `MEM_ADMIN_TOKEN` | не задана | если задана, сервис при каждом старте создаёт клиента `admin` с этим токеном (см. [Локальный администратор](#local-admin)) |
| `MEM_STATUS_SECRET` | не задана | включает `GET /status/<secret>`; без неё маршрут отвечает `404` |
| `MEM_MCP_LANG` | `en` | язык описаний инструментов MCP: `en` или `ru` |
| `MEM_TZ` | `UTC` | часовой пояс расписания сна (имя IANA, например `Europe/Moscow`), читает исполнитель |
| `MEM_TRUSTED_SCOPES` | `owner,dev,session,repo,file,import,cowork,code,claude,crm` | префиксы `source_scope` доказательств, которые считаются собственной работой клиента (см. [concepts.md](concepts.md#trust)) |
| `MEM_EXPORT_DIR` | не задана (`/exports` у исполнителя) | куда лёгкий сон пишет выгрузки JSONL; не задана → стадия выгрузки пропускается |
| `MEM_DB_APP_ROLE` | `mem_app` | роль базы, на которую переключается сервис, чтобы действовала защита на уровне строк; пустое значение — только если вы понимаете зачем |
| `MEM_ALERT_WEBHOOK_URL` | не задана | оповещения: POST `{"text": "..."}` на этот адрес |
| `MEM_TG_BOT_TOKEN`, `MEM_TG_CHAT_ID` | не заданы | оповещения в Telegram (нужны обе) |
| `MEM_TG_PROXY` | не задана | `socks5://host:port`: отправлять оповещения Telegram через этот прокси SOCKS5 |
| `MEM_ANTHROPIC_API_KEY` | не задана | ключ для моделей Anthropic в глубоком сне |
| `MEM_DEEP_MODEL_REASON` | не задана | модель глубокого сна для расхождений и предложений слотов; не задана → эти стадии `DISABLED` |
| `MEM_DEEP_MODEL_SUMMARY` | не задана | модель глубокого сна для сводок эпизодов; не задана → эта стадия `DISABLED` |
| `MEM_OPENAI_BASE_URL` | не задана | OpenAI-совместимый адрес внутри вашей сети, например `http://llm.internal:8000/v1` |
| `MEM_OPENAI_API_KEY` | не задана | если задана, передаётся этому адресу как `Authorization: Bearer` |
| `MEM_OPENAI_IN_CONTOUR_MODELS` | не задана | имена моделей через запятую, разрешённые на маршруте `openai:` |
| `MEM_STAND` | `0` | `1` включает режим тестового стенда: часы можно сдвигать. Никогда — в продакшене |
| `MEM_CLOCK_OFFSET` | `0` | начальный сдвиг часов в секундах, только при `MEM_STAND=1` |

Образ также задаёт `FASTEMBED_CACHE_PATH=/models` и `HF_HOME=/models/hf`, чтобы модель попадала в том `mem_models`.

**Модель эмбеддингов закрепляется за базой.** Первый старт записывает модель и размерность; если они потом изменятся,
сервис не запустится. Встроенной команды пересчёта векторов нет, поэтому выберите модель до первого старта.

<a id="local-admin"></a>

## Локальный администратор

Право `admin` действует только для запросов **напрямую** к сервису. Запрос с заголовком `X-Real-IP` или
`X-Forwarded-For` считается внешним, и тогда у токена администратора нет доступа ни к чему. Это проверка заголовка, а
не IP-адреса:

- обратный прокси должен всегда ставить `X-Real-IP` (или `X-Forwarded-For`), иначе внешние вызовы окажутся
  «локальными»;
- любой процесс, который может достучаться до порта 3120 напрямую, включая контейнеры в сети `mem_clients`, считается
  локальным. Никогда не давайте такому процессу токен администратора; дайте ему обычный токен клиента.

`MEM_ADMIN_TOKEN` создаёт клиента `admin` (участник `admin` в организации `system`) со всеми правами во всех
пространствах. Сгенерируйте его командой `openssl rand -hex 32`. Токен записывается при каждом старте: чтобы сменить
его, поменяйте значение и перезапустите сервис. Если убрать переменную, сохранённый токен не удалится, поэтому вместо
этого смените его на новое случайное значение.

Маршруты REST только для администратора: `POST /v1/_admin/sleep/run`, `POST /v1/{ns}/import`, `GET /v1/{ns}/export`
(также владельцам пространства), `POST /v1/_stand/clock` (только при `MEM_STAND=1`).

<a id="cli"></a>

## CLI

Организациями, людьми, группами, пространствами, грантами и токенами управляет `python -m mem.cli`, он запускается
внутри контейнера `api`. CLI работает с базой напрямую, и токен ему не нужен.

```bash
docker compose exec api python -m mem.cli <command> [options]
```

Команды по возможности идемпотентны: создание того, что уже есть, ничего не меняет (у участника обновляются допуск и
отображаемое имя). Ошибкой считаются id, который уже принадлежит другой организации, участнику другого вида или
действующему от чужого имени, а также пространство с другой организацией или владельцем: команда пишет причину в stderr
и завершается с кодом 1, так что скрипт установки на этом остановится. Агент может действовать только от имени человека
из своей организации.

| Команда | Аргументы и флаги | Что делает |
|---|---|---|
| `init` | `--tenant ID` `--person ID` `[--name TEXT]` `[--ns ID]` `[--agent ID]` `[--profile work\|assistant\|team]` | первый запуск: организация, человек (допуск 3), личное пространство `<person>.<profile>` с грантом `owner` до уровня 3, агент `<person>-agent`, действующий от имени человека, и его токен (`read,write,review` в этом пространстве). Печатает токен один раз. Повторный запуск выпускает токен заново |
| `create-tenant` | `ID NAME` | организация |
| `create-principal` | `ID --tenant ID` `[--kind user\|agent\|service]` (по умолчанию `user`) `[--on-behalf-of ID]` (обязателен для агентов) `[--clearance 0-3]` (по умолчанию 1) `[--name TEXT]` | человек, агент или сервис |
| `create-group` | `ID --tenant ID --name TEXT` `[--parent ID]` `[--kind team\|org_unit\|project\|role]` (по умолчанию `team`) | группа, при желании вложенная |
| `add-member` | `GROUP PRINCIPAL` `[--role member\|curator]` | добавляет участника в группу (роль участника сама по себе ничего не даёт) |
| `create-namespace` | `ID --tenant ID --scope personal\|group\|tenant` `[--owner-principal ID]` `[--owner-group ID]` `[--classification 0-3]` (по умолчанию 1) `[--profile work\|assistant\|team]` (по умолчанию `work`) `[--description TEXT]` | пространство (слой). После этого перезапустите API, чтобы MCP его обслуживал |
| `grant` | `NS principal\|group\|tenant SUBJECT reader\|contributor\|curator\|owner` `[--max-class 0-3]` (по умолчанию 1) `[--expires ISO-DATE]` | даёт роль в пространстве; повторный грант обновляет её |
| `revoke` | `NS SUBJECT_TYPE SUBJECT_ID` | убирает грант |
| `create-client` | `--id ID --ns NS[,NS...]\|* --rights LIST` `[--principal ID]` (по умолчанию = `--id`) `[--delegate-group GROUP]` | создаёт токен или заменяет токен существующего клиента. Права: `read,write,review,forget,manage,admin`. Печатает токен один раз |
| `check-client` | `NS` `[--need LIST]` (по умолчанию `read`), токен на stdin | что токен может в пространстве так, как это видит внешний запрос (организация, права, потолок классификации). Завершается с кодом 1, если не хватает любого права из `--need` или токен неизвестен. Проверяйте так токен, прежде чем отдать его агенту: `docker compose exec -T api python -m mem.cli check-client alice.work --need read,write < token.txt` (`-T` пропускает stdin) |
| `list-clients` | — | id, участник, пространства, права, когда был в последний раз |
| `offboard` | `PRINCIPAL` `[--purge-after-days N]` (по умолчанию 30) | человек уходит: он и его агенты деактивируются, их токены удаляются, участие в группах снимается, личные пространства назначаются к удалению лёгким сном |
| `purge-namespace` | `NS --yes` | сразу удаляет все записи пространства (необратимо) |
| `forget-subject` | `TENANT SUBJECT` `[--confirm N]` | удаляет все записи о субъекте и его псевдонимах во всей организации. Без `--confirm` — пробный прогон, печатает число; передайте это число, чтобы применить |

Пространства и права токена — это потолок: что он может на самом деле, зависит ещё от грантов человека, которому он
принадлежит. Пример для команды:

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

Токен агента с правом `forget` (чтобы работал инструмент `forget`) выпускается так же:
`create-client --id alice-agent --ns alice.work --rights read,write,review,forget` (старый токен заменяется). Для
выгрузки пространства через REST нужен токен с `manage`, чей человек имеет там роль `owner`, либо локальный
администратор.

Настройки пространства, для которых нет команды CLI, хранятся в `namespaces.settings` (JSON). Например, чтобы
принимать переносы некоторых видов в слой команды без куратора:

```bash
docker compose exec postgres psql -U mem -d mem -c \
  "UPDATE namespaces SET settings = settings || '{\"promotion_auto\": [\"topic_fact\"]}' WHERE id = 'acme.eng'"
```

<a id="running-behind-a-tls-reverse-proxy"></a>

## Работа за обратным прокси с TLS

Держите порт 3120 на `127.0.0.1` и публикуйте только `/v1/` и `/mcp/` через прокси с TLS. Прокси **обязан** ставить
`X-Real-IP`, чтобы внешние запросы никогда не считались запросами локального администратора.

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

Caddy сам получает сертификаты и по умолчанию добавляет `X-Forwarded-For`; в примере `X-Real-IP` тоже задан явно.

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

Проверьте снаружи, что токен администратора отклоняется: `curl -X POST https://mem.example.com/v1/<ns>/import ...`
должен вернуть `403` (или `404` для `/v1/_admin/...` с конфигурациями выше).

<a id="the-worker-and-the-sleep-schedule"></a>

## Исполнитель и расписание сна

Контейнер `worker` запускает сон в фиксированное местное время в часовом поясе `MEM_TZ` (по умолчанию `UTC`):

| Задача | Когда | Примечание |
|---|---|---|
| лёгкий сон | каждый день в 03:30 | детерминированный, без модели; занимает около 60 МБ |
| глубокий сон | по воскресеньям в 04:00 | загружает модель эмбеддингов на время прогона (сводки — это новые записи) и вызывает модель глубокого сна |

Само время зашито в `mem/jobs.py`; выберите `MEM_TZ` так, чтобы 03:30 приходилось на тихие часы. Плановый прогон
пропускается, если настоящий прогон того же вида уже завершился в этот день со статусом `OK` или `PARTIAL`. Что делает
каждая стадия — в [concepts.md](concepts.md#sleep).

Запуск сна вручную:

```bash
docker compose exec worker python -m mem.jobs light-now --dry-run   # counts only, nothing changes
docker compose exec worker python -m mem.jobs light-now
docker compose exec worker python -m mem.jobs deep-now --dry-run
```

Команда печатает результат и завершается с кодом `0`, если статус `OK` (или `SKIPPED`, потому что прогон уже идёт).
Локальный администратор может запустить сон и через REST (`POST /v1/_admin/sleep/run`); тогда он выполняется внутри
контейнера `api`, где `MEM_EXPORT_DIR` не задана, и стадия выгрузки пропускается.

Отчёты читаются через `GET /v1/<ns>/sleep/latest?kind=light|deep` или инструмент MCP `sleep_report`.

**Отключение стадий.** Любую стадию любого сна можно отключить без выкатки; тогда её статус — `DISABLED`. Стадии с
моделью, для которых модель не настроена, и так получают `DISABLED`. Чтобы отключить стадии с моделью даже при
настроенных моделях:

```bash
docker compose exec postgres psql -U mem -d mem -c \
  "INSERT INTO meta(key, value) VALUES ('sleep_disabled_stages', 'contradictions,slot_suggestions,summaries')
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
```

Имена стадий: лёгкий сон — `decay`, `dedup_exact`, `promote`, `forget`, `retention`, `review_expiry`, `maintenance`,
`export`; глубокий — `contradictions`, `slot_suggestions`, `stale`, `summaries`.

**Выгрузки.** Если задана `MEM_EXPORT_DIR` (у исполнителя это `/exports`), лёгкий сон пишет `<namespace>-YYYYMMDD.jsonl`
на каждое пространство и хранит по 14 последних. Забрать их можно командой
`docker compose cp worker:/exports ./exports`. В них записи и доказательства, а не вся база; для резервных копий
используйте `pg_dump`.

## Маршруты модели глубокого сна

Глубокий сон использует две модели: `MEM_DEEP_MODEL_REASON` (расхождения, предложения слотов) и
`MEM_DEEP_MODEL_SUMMARY` (сводки эпизодов). Модели по умолчанию нет: стадия, для которой модель не задана, получает
`DISABLED`, и никуда ничего не отправляется, даже если ключ API задан. Маршрут выбирается по префиксу в имени модели:

| Имя модели | Маршрут | Что нужно |
|---|---|---|
| `claude-...` (без префикса) или `anthropic:claude-...` | Anthropic Messages API | `MEM_ANTHROPIC_API_KEY` |
| `openai:<model>` | OpenAI-совместимый `chat/completions` внутри вашей сети | `MEM_OPENAI_BASE_URL`, при необходимости `MEM_OPENAI_API_KEY`, и `<model>` в списке `MEM_OPENAI_IN_CONTOUR_MODELS` |
| всё остальное (`yandex:`, `gigachat:`, неизвестные префиксы, имя без префикса не на `claude...`) | не поддерживается | — |

Пример с моделью на вашем собственном сервере инференса:

```bash
MEM_OPENAI_BASE_URL=http://llm.internal:8000/v1
MEM_OPENAI_API_KEY=<key-if-your-server-needs-one>
MEM_OPENAI_IN_CONTOUR_MODELS=my-model
MEM_DEEP_MODEL_REASON=openai:my-model
MEM_DEEP_MODEL_SUMMARY=openai:my-model
```

Правила, которые проверяет код:

- На облачном маршруте тексты записей покинули бы ваш периметр, а маскирования в Onfim нет, поэтому разрешены только
  Anthropic и модели, которые вы размещаете сами. `MEM_OPENAI_BASE_URL` должен иметь вид `http(s)://host[:port]/path`
  без учётных данных и параметров запроса и отклоняется, если указывает на известного облачного провайдера (OpenAI,
  Azure OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Google, Groq, Together, Yandex, Sber). Переменные окружения
  прокси для этого адреса игнорируются.
- Модель должна отвечать через принудительный вызов инструмента по JSON-схеме (на маршруте `openai:` — с температурой
  0); ваш сервер должен поддерживать вызов инструментов. Негодный ответ — ошибка стадии, а не пустое предложение. При
  тайм-аутах и ответах 429/5xx запрос повторяется до трёх раз.
- Ошибки настройки (нет ключа, модели нет в списке, неверный адрес) обнаруживаются до первого вызова: стадии с моделью
  завершаются ошибкой, прогон получает `PARTIAL`, отправляется оповещение, и ни одной модели ничего не отправляется.
- Записи с классификацией 3 или `visibility: owner_only` не отправляются никогда, какой бы ни был маршрут.

## Оповещения

Оповещение отправляется, когда сон завершился со статусом не `OK` (включая пробные прогоны), когда модель вернула
негодные пункты и их пришлось пропустить и когда плановая задача упала. Каждое оповещение пишется в журнал как
предупреждение (`ALERT: ...`); кроме того:

- `MEM_ALERT_WEBHOOK_URL` — POST с JSON `{"text": "..."}`. Подходит для входящих вебхуков Slack и Mattermost или для
  своего приёмника.
- `MEM_TG_BOT_TOKEN` и `MEM_TG_CHAT_ID` — сообщение через Telegram Bot API. Если Telegram доступен с вашего хоста
  только через туннель, задайте `MEM_TG_PROXY=socks5://host:port`; при неверном значении оповещения в Telegram не
  отправляются (это пишется в журнал), а не уходят в обход прокси.

Можно задать оба способа. Тексты на английском и обрезаются до 3500 символов. Неудачная доставка пишется в журнал и
никогда не ломает сон. Оповещения отправляет процесс, который выполнял задачу (обычно исполнитель), поэтому задавайте
эти переменные в `.env`, который читают оба контейнера.

## Мониторинг

- `GET /healthz` → `{"ok": true}`; его использует проверка здоровья в Compose (на первый старт заложите около двух
  минут).
- `GET /status/<MEM_STATUS_SECRET>` → состояние базы, модель, число записей по пространствам и состояниям, квартили
  силы, последний лёгкий и глубокий сон со статусом, число открытых пунктов очереди. Текстов нет.
- `docker compose logs api worker` — API при старте пишет `mem started: model=... namespaces=[...]`; исполнитель пишет
  время следующих прогонов. Тексты записей и цитаты в журнал не попадают никогда.

## Резервные копии

Делайте копию базы через `pg_dump` по расписанию и храните копии вне хоста, в зашифрованном виде:

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U mem -d mem -Fc > backups/mem-$(date +%F).dump
```

Восстановление в ту же установку:

```bash
docker compose stop api worker
docker compose exec -T postgres psql -U mem -d postgres -c "DROP DATABASE mem" -c "CREATE DATABASE mem OWNER mem"
docker compose exec -T postgres pg_restore -U mem -d mem < backups/mem-2026-10-09.dump
docker compose up -d api worker
```

Восстановление на новом хосте: сначала запустите только `postgres` и создайте служебную роль, которой нет в `pg_dump`
(это роль всего кластера), затем восстановите, как выше:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U mem -d mem -c "CREATE ROLE mem_app NOLOGIN NOSUPERUSER NOBYPASSRLS"
```

Используйте те же `MEM_EMBED_MODEL` и `MEM_EMBED_DIM`, что в исходной установке, иначе API не запустится. Проверьте
восстановление заранее, а не когда оно понадобится.

## Обновление

```bash
docker compose exec -T postgres pg_dump -U mem -d mem -Fc > backups/mem-pre-upgrade-$(date +%F).dump
docker tag mem-api:latest mem-api:pre-upgrade      # keep the old image for a rollback
git pull
docker compose build api                           # the worker uses the same image
docker compose up -d api worker
docker compose logs -f api                         # look for "migration applied: ..." and "mem started"
```

Миграции — SQL-файлы в `migrations/` с именами `NNNN_*.sql`. API при старте применяет недостающие по порядку, каждую в
своей транзакции, под блокировкой базы, и записывает каждую по **имени файла** в `schema_migrations`. После выпуска
файлы не редактируются; изменение — всегда новый файл. API не запустится, если какой-то миграции не хватает или если
модель или размерность эмбеддингов в базе отличается от настроек. Исполнитель миграции не применяет, поэтому сначала
запускайте API (Compose так и делает).

Откат: остановите `api` и `worker`, восстановите копию, сделанную перед обновлением, выполните
`docker tag mem-api:pre-upgrade mem-api:latest` и запустите их снова. Более новую миграцию на месте отменить нельзя.

## Чек-лист

- `.env` нет в git; `MEM_DB_PASSWORD` и `MEM_ADMIN_TOKEN` случайные.
- Порт 3120 привязан к `127.0.0.1`; наружу открыт только прокси, и он ставит `X-Real-IP`.
- В продакшене `MEM_STAND` не задана.
- Резервные копии делаются, восстановление проверено.
- Оповещения до кого-то доходят; модель глубокого сна настроена или её стадии отключены.
