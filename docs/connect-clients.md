# Connecting clients

**English** · [Русский](ru/connect-clients.md)

Onfim speaks MCP over streamable HTTP with a bearer token in the `Authorization` header (details in [mcp.md](mcp.md)).
This page shows how to add it to Claude Code, Claude Desktop and Cursor.

Client configuration formats change. The Claude Code and Claude Desktop instructions below were checked against the
vendors' documentation in October 2026; the Cursor section could not be checked and is marked as such.

## Before you start

You need three things:

1. **A namespace and a token.** On the server:

   ```bash
   docker compose exec api python -m mem.cli init --tenant acme --person alice --name "Alice"
   docker compose restart api        # MCP picks up the new namespace
   ```

   `init` prints the token once, together with the MCP path (`/mcp/alice.work/`). Running `init` again re-issues the
   agent's token; the old one stops working.

2. **The URL.**
   - On the same machine as the service: `http://127.0.0.1:3120/mcp/alice.work/`
   - From anywhere else: put the service behind a TLS reverse proxy
     ([operations.md](operations.md#running-behind-a-tls-reverse-proxy)) and use
     `https://mem.example.com/mcp/alice.work/`. Do not expose port 3120 directly.

   Keep the trailing slash.

3. **A check that it works:**

   ```bash
   curl -sS -X POST http://127.0.0.1:3120/mcp/alice.work/ \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
   ```

   A list of 16 tools means the endpoint and the token are fine. `401` means a wrong token, `403` a namespace the
   token is not for, `404` a namespace the API has not loaded yet (restart it).

Use a person's agent token in clients, never the admin token: through a proxy the admin token has no access at all,
and directly it can read every organization.

## Claude Code

Add the server with the CLI:

```bash
claude mcp add --transport http onfim http://127.0.0.1:3120/mcp/alice.work/ \
  --header "Authorization: Bearer <token>"
```

For a remote service:

```bash
claude mcp add --transport http onfim https://mem.example.com/mcp/alice.work/ \
  --header "Authorization: Bearer <token>"
```

`--scope` chooses where the configuration is stored: `local` (default, this project, only you), `user` (all your
projects) or `project` (written to `.mcp.json` in the repository and shared with everyone who uses it).

Do not commit a token. For a shared `.mcp.json`, reference an environment variable instead; Claude Code expands
`${VAR}` in `url` and `headers`:

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

Then each person sets `ONFIM_TOKEN` to their own token. Use your own variable name: Claude Code does not expand some
well-known credential variables (for example `ANTHROPIC_API_KEY`) into remote server headers.

Check with `claude mcp list`, or `/mcp` inside a session. The server instructions already tell Claude Code which
kinds to use and how to fill in evidence; a line in your `CLAUDE.md` such as "Before starting a task, `recall` memory
with the project's tag; after a decision or a pitfall, `remember` it" makes it use memory consistently.

## Claude Desktop

Claude Desktop adds remote MCP servers as **custom connectors**. The connection is made from Anthropic's cloud, not
from your computer, so the server must be reachable over the public internet with HTTPS:
`https://mem.example.com/mcp/alice.work/`. A `127.0.0.1` address will not work here. If your proxy restricts source
addresses, allow Anthropic's published IP ranges.

1. Open **Customize → Connectors** (Settings → Connectors in some versions), click **+ Add** and choose
   **Add custom connector**.
2. Enter a name (`Onfim`) and the URL `https://mem.example.com/mcp/alice.work/`, then **Continue**.
3. Under **Authentication**, choose **No sign in**.
4. Under **Request headers**, add `Authorization` with the value `Bearer <token>`.
5. Click **Add**, then enable the connector in a conversation from the **+** button → **Connectors**.

Availability of custom connectors depends on your plan (free plans are limited to one).

**Not verified:** to reach a service that is only on your own machine or network, a common workaround is the
community `mcp-remote` package, which runs locally as a stdio server and forwards to the HTTP endpoint. In
`claude_desktop_config.json` it would look like this (requires Node.js):

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

Check the `mcp-remote` documentation for the current flags before relying on this.

## Cursor

**Not verified against Cursor's current documentation.** Cursor reads MCP servers from `~/.cursor/mcp.json` (all
projects) or `.cursor/mcp.json` in a project. A remote HTTP server with a header is configured like this:

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

For a local service use `http://127.0.0.1:3120/mcp/alice.work/`. After saving, check the server and its tools in
Cursor's MCP settings. Do not commit a project `mcp.json` that contains a token.

## Your own agents

Anything that speaks MCP over streamable HTTP can connect the same way. Bots and services can also use the REST API
directly ([rest-api.md](rest-api.md)); a team agent that acts for several people must use REST with a delegated token
and the `X-Mem-On-Behalf-Of` header.
