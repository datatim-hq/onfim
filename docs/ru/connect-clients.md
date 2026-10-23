# Подключение клиентов

[English](../connect-clients.md) · **Русский**

Onfim работает по MCP через streamable HTTP, токен передаётся в заголовке `Authorization` (подробности — в
[mcp.md](mcp.md)). На этой странице — как подключить Onfim к Claude Code, Claude Desktop и Cursor.

Форматы настроек у клиентов меняются. Инструкции для Claude Code и Claude Desktop сверены с документацией
производителей в октябре 2026 года; раздел про Cursor сверить не удалось, он помечен.

## Перед началом

Нужны три вещи.

1. **Пространство и токен.** На сервере:

   ```bash
   docker compose exec api python -m mem.cli init --tenant acme --person alice --name "Alice"
   docker compose restart api        # MCP подхватывает новое пространство
   ```

   `init` один раз печатает токен и путь MCP (`/mcp/alice.work/`). Повторный `init` выпускает агенту новый токен,
   старый перестаёт работать.

2. **Адрес.**
   - На той же машине, что и сервис: `http://127.0.0.1:3120/mcp/alice.work/`
   - Откуда угодно ещё: поставьте сервис за обратный прокси с TLS
     ([operations.md](operations.md#running-behind-a-tls-reverse-proxy)) и используйте
     `https://mem.example.com/mcp/alice.work/`. Порт 3120 наружу не открывайте.

   Слэш в конце адреса обязателен.

3. **Проверка, что всё работает:**

   ```bash
   curl -sS -X POST http://127.0.0.1:3120/mcp/alice.work/ \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
   ```

   Список из 16 инструментов — адрес и токен в порядке. `401` — неверный токен, `403` — токен выдан не для этого
   пространства, `404` — API ещё не загрузил пространство (перезапустите его).

В клиентах используйте токен агента конкретного человека, а не токен администратора: через прокси у администратора
доступа нет совсем, а напрямую он читает все организации.

## Claude Code

Сервер добавляется командой:

```bash
claude mcp add --transport http onfim http://127.0.0.1:3120/mcp/alice.work/ \
  --header "Authorization: Bearer <token>"
```

Для удалённого сервиса:

```bash
claude mcp add --transport http onfim https://mem.example.com/mcp/alice.work/ \
  --header "Authorization: Bearer <token>"
```

`--scope` задаёт, где хранится настройка: `local` (по умолчанию: этот проект, только вы), `user` (все ваши проекты)
или `project` (файл `.mcp.json` в репозитории, общий для всех, кто с ним работает).

Токен в репозиторий не коммитьте. В общем `.mcp.json` сошлитесь на переменную окружения: Claude Code подставляет
`${VAR}` в `url` и `headers`.

```json
{
  "mcpServers": {
    "onfim": {
      "type": "http",
      "url": "https://mem.example.com/mcp/alice.work/",
      "headers": {
        "Authorization": "Bearer ${ONFIM_TOKEN}"
      }
    }
  }
}
```

Каждый участник задаёт `ONFIM_TOKEN` со своим токеном. Имя переменной выберите своё: некоторые известные переменные
с ключами (например, `ANTHROPIC_API_KEY`) Claude Code в заголовки удалённых серверов не подставляет.

Проверка — `claude mcp list` или `/mcp` внутри сессии. Инструкции сервера уже объясняют Claude Code, какие виды
записей использовать и как заполнять доказательства. Чтобы память использовалась постоянно, добавьте в `CLAUDE.md`
строку вроде «Перед задачей делай `recall` по тегу проекта; после решения или найденной грабли — `remember`».

## Claude Desktop

Claude Desktop добавляет удалённые MCP-серверы как **пользовательские коннекторы**. Подключение идёт из облака
Anthropic, а не с вашего компьютера, поэтому сервер должен быть доступен из интернета по HTTPS:
`https://mem.example.com/mcp/alice.work/`. Адрес `127.0.0.1` здесь не подойдёт. Если прокси ограничивает адреса
источников, разрешите опубликованные диапазоны IP Anthropic.

1. Откройте **Customize → Connectors** (в некоторых версиях Settings → Connectors), нажмите **+ Add** и выберите
   **Add custom connector**.
2. Введите имя (`Onfim`) и адрес `https://mem.example.com/mcp/alice.work/`, нажмите **Continue**.
3. В разделе **Authentication** выберите **No sign in**.
4. В разделе **Request headers** добавьте `Authorization` со значением `Bearer <token>`.
5. Нажмите **Add** и включите коннектор в разговоре через кнопку **+** → **Connectors**.

Доступность пользовательских коннекторов зависит от тарифа (на бесплатном — один).

**Не проверено:** чтобы достучаться до сервиса, который есть только на вашей машине или в вашей сети, часто
используют пакет сообщества `mcp-remote`. Он запускается локально как stdio-сервер и пересылает запросы на HTTP-адрес.
В `claude_desktop_config.json` это выглядит так (нужен Node.js):

```json
{
  "mcpServers": {
    "onfim": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:3120/mcp/alice.work/",
               "--header", "Authorization:${ONFIM_AUTH}"],
      "env": { "ONFIM_AUTH": "Bearer <token>" }
    }
  }
}
```

Прежде чем на это полагаться, сверьте актуальные флаги с документацией `mcp-remote`.

## Cursor

**Не сверено с актуальной документацией Cursor.** Cursor читает MCP-серверы из `~/.cursor/mcp.json` (все проекты)
или из `.cursor/mcp.json` в проекте. Удалённый HTTP-сервер с заголовком настраивается так:

```json
{
  "mcpServers": {
    "onfim": {
      "url": "https://mem.example.com/mcp/alice.work/",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Для локального сервиса — `http://127.0.0.1:3120/mcp/alice.work/`. После сохранения проверьте сервер и его
инструменты в настройках MCP в Cursor. Проектный `mcp.json` с токеном не коммитьте.

## Свои агенты

Всё, что умеет MCP через streamable HTTP, подключается так же. Боты и сервисы могут работать и напрямую с REST API
([rest-api.md](rest-api.md)). Агент команды, который действует от имени нескольких людей, должен использовать REST
с делегированным токеном и заголовком `X-Mem-On-Behalf-Of`.
