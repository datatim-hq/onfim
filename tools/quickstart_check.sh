#!/usr/bin/env bash
# Quick start from README.md on a clean machine: the same commands, then one write and one search over REST
# and the MCP tool list with the token printed by `init`. Run from the repository root; needs Docker Compose v2.
set -euo pipefail

cp .env.example .env && sed -i "s/change-me/$(openssl rand -hex 16)/" .env
docker compose up -d --build --wait --wait-timeout 900
out=$(docker compose exec -T api python -m mem.cli init --tenant acme --person alice --name "Alice")
echo "$out" | grep -v '^token'
token=$(echo "$out" | sed -n 's/^token (shown once): //p')
test -n "$token"
docker compose restart api
docker compose up -d --wait --wait-timeout 300

base=http://127.0.0.1:3120
auth="Authorization: Bearer $token"
json="Content-Type: application/json"
curl -fsS -X POST "$base/v1/alice.work/memories" -H "$auth" -H "$json" -d '{
  "text": "Deploys go through make release", "kind": "convention",
  "evidence": [{"source_ref": "quickstart", "source_scope": "dev", "observed_at": "2026-10-09T12:00:00+00:00"}]}' >/dev/null
found=$(curl -fsS -X POST "$base/v1/alice.work/recall" -H "$auth" -H "$json" -d '{"query": "how do we deploy?"}')
grep -q "make release" <<<"$found"
tools=$(curl -fsS -X POST "$base/mcp/alice.work/" -H "$auth" -H "$json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}')
grep -q '"remember"' <<<"$tools"
echo "quick start OK"
