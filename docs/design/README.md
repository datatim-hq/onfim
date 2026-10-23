# Onfim (mem) — design specs

**English** · [Русский](../ru/design/README.md)

> The product is Onfim; the package and the service in the code are `mem`. The specs were written in Russian during
> development; this is their English translation, and the Russian originals are in
> [`docs/ru/design/`](../ru/design/README.md).
> Status: **v0.3** (08 — access model, 09 — team memory); v0.2 — sleep, strength, supersession, forgetting.

## Why

A check of the memory service we used before (23–24 Sept 2026) showed that it works as storage, but everything it
does "on its own" — sorting into layers, consolidation, forgetting, the sleep report — either does not work or works
in a way you cannot rely on. Other ready-made solutions we looked at do not forget reliably either. Decision: **our own
memory on Postgres, where every automatic action is an explicit rule with a test**. The logic we wanted from the
previous service stays: **sleep cycles, ranking knowledge by strength, replacing old claims with confirmed new ones,
forgetting** (05). In addition: evidence with quotes for every fact, a weekly clean-up with a "stale / disputed" report,
and a disclosure boundary for personal facts.

Clients:
1. **A personal assistant agent** — memory about people, chats and topics (a namespace with the `assistant` profile).
2. **AI coding assistants (Claude Code, Claude Desktop, Cursor)** — working development memory: decisions made on the
   fly, pitfalls, conventions (the `work` namespace).

## Principles (derived from the previous service's defects)

| # | Principle | Which defect of the previous service it closes |
|---|---|---|
| P1 | **The service does not rewrite what the client sent.** No LLM on the write path: text, kind (`kind`), slot, tags and importance are stored as is. Only state and strength change, by the rules of 05 | LLM extraction rewrote the explicit layer; the layer classifier did not work on Russian |
| P2 | **Every automatic action is a formula or a gate (G1–G3) from 05,** logged and tested; the deep-sleep LLM only proposes, and its result passes a deterministic check | Forgetting by "importance", which was the same for everything; a sleep summary distorted a date |
| P3 | **An old claim is replaced only by a confirmed new one**: explicitly by the client, or through a slot and gate G1 (newer + confirmed independently / by the owner / by the author themselves); disputes go to a human queue; history and 30-day rollback | Contradictions were not handled |
| P4 | **Deletion is real** (purge); soft deletion is a separate operation | `DELETE` left the text and the vector behind |
| P5 | **The nightly job reports honestly:** any stage error → status `FAILED`/`PARTIAL` and an alert | `COMPLETED` with a failed consolidation |
| P6 | **Search ranking is described by a formula and covered by tests** | A constant score, broken BM25 fusion |
| P7 | **Filtering by tags and namespace works the same over REST and MCP** — one code path | Tags were ignored over MCP |
| P8 | **No service records in client namespaces** | Entities and markers crowded out real content |
| P9 | **Embeddings are computed locally** — texts are not sent to external providers | Facts from private chats were sent to OpenAI |
| P10 | **The database schema = the migrations**, CI checks for drift | The enum `DREAM_CYCLE` was in the schema but not in the migrations |
| P11 | **Strength of knowledge is a separate value, not importance**: it grows from independent confirmations and use, decays by the kind's half-life, and recomputing never raises it | A floor of 0.35 flattened all memory; service records got 1.0 |
| P12 | **Every fact has evidence** (source, speaker, date, a short quote); answers and clean-up reports show the quotes | Sleep patterns with no link to sources |
| P13 | **Disclosure boundary**: a record knows where it may be shown (`visibility`), and search with `audience` respects it | — |
| P14 | **Corporate access model from day one**: organizations, principals, nested groups, memory layers (personal → team → department → organization), grants with roles and confidentiality levels of records; an agent never sees more than its person; the filter is in SQL before ranking (08) | The previous service: one user, all-or-nothing access |

## The specs

| No. | File | About |
|---|---|---|
| 00 | [00-requirements.md](00-requirements.md) | Purpose, scenarios, requirements, non-goals |
| 01 | [01-architecture.md](01-architecture.md) | Components, deployment, resources, security |
| 02 | [02-data-model.md](02-data-model.md) | Tables, kinds, slots, strength, evidence, the review queue, audit |
| 03 | [03-api.md](03-api.md) | REST and MCP: operations, formats, authorization |
| 04 | [04-search.md](04-search.md) | Hybrid search, ranking with strength, the disclosure boundary |
| 05 | [05-lifecycle.md](05-lifecycle.md) | **Memory strength, light and deep sleep, replacement by a confirmed new claim, forgetting** — the core of the service |
| 07 | [07-verification.md](07-verification.md) | Acceptance: canaries with simulated time, tests, monitoring |
| 08 | [08-access-model.md](08-access-model.md) | **Organizations, users, groups, memory layers, grants, classification, agent delegation** |
| 09 | [09-team-memory.md](09-team-memory.md) | **Team memory:** delegation to an agent service, the speaker as a principal, trust in a team, moving between layers, a member leaving, an external entity key for CRM |

Format (Spec Pattern Library): Purpose → Scenarios → Functional requirements (co-authorship 0–4, **[DET]** for
deterministic) → Acceptance criteria → Lessons from production.
