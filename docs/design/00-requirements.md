# 00. Requirements

**English** · [Русский](../ru/design/00-requirements.md)

## Purpose

Long-term memory for two kinds of clients — a personal assistant agent and AI coding assistants (Claude Code, Claude
Desktop, Cursor) — with search over Russian and English text, an explicit history of how facts change, predictable
forgetting and real deletion. The service stores, finds, **runs the life cycle of knowledge** (strength, sleep,
supersession, forgetting — 05) and hands disputes to a human; the client extracts facts from raw text.

## Scenarios

- **M-S1. Claude Code hits a pitfall** → `remember(namespace=work, kind=gotcha, text="Shop: Next.js redirect via request.url goes to localhost:3002…", tags=[project:shop, nextjs])`. A week later, in a new session, `recall("next.js redirect behind nginx", tags=[project:shop])` returns the record first.
- **M-S2. Cowork starts working on a project** → the `project-context-boot` skill calls `recall` with the project tag and gets the decisions and pitfalls, sorted by freshness and kind.
- **M-S3. The agent writes a fact about a person** → `remember(namespace=assistant, kind=person_fact, subject=person:petrov, text="Alexei Petrov is the sauna contractor, answers in the evening")`.
- **M-S4. A fact changed** → "Petrov is now the contractor for the fence too" → `remember(..., slot=person:petrov#role)`. The slot is taken, so the new record becomes a candidate; Petrov himself confirmed it in another message → at night the light sleep replaces the old one (G1), and the clean-up report shows both wordings with quotes. Had a third person said it with no confirmation, a "contradiction" item would appear in the queue after 14 days.
- **M-S4a. Knowledge gets stronger** → three people in different chats said that Ivanova answers only in writing → the record's strength grows; in the briefing and in search it ranks above a fact mentioned once.
- **M-S5. The agent's summary of the day** → `remember(kind=episode, subject=chat:dacha, text="24.09: discussed the estimate…")`. A month later the deep sleep folds the month's episodes into a summary (if every date and number of the summary is in the sources — G2); the episodes fade and go to the archive after about 3 months, while facts about people that get confirmed and used live on.
- **M-S6. "Forget everything about Sidorov"** → the agent calls `forget(subject=person:sidorov, mode=purge)`. Records and vectors are physically deleted; the audit keeps only the fact of the operation, without text.
- **M-S7. Sunday clean-up** → a report: what was replaced (with quotes), what was forgotten, what is "stale?", which disputes wait for a decision. The agent shows it in the Sunday evening report and the owner goes through it with buttons; in Cowork, with the `review_list` tool. Any stage error → an alert to Telegram.
- **M-S9. An answer with a source** → "When is a good time to call Chizhov?" → "In the mornings — according to Chizhov himself, chat "Nest", 22.09" (evidence from `evidence`).
- **M-S8. Looking at memory directly** → `list(namespace=work, tags=[project:shop])` from Cowork over MCP.

## Functional requirements

| ID | Requirement | Co-auth. | Type |
|---|---|---|---|
| F-1 | A record keeps `text`, `kind`, `subject`, `tags`, `importance`, `source`, `occurred_at` exactly as the client sent them | 4 | DET |
| F-2 | Namespaces (`namespace`) are isolated: a client token allows a list of namespaces; a request to another one → 403 | 4 | DET |
| F-3 | Hybrid search: vector (a local multilingual model) + full text (Postgres FTS, `russian` and `english` dictionaries) + filters by namespace, `kind`, `subject`, tags (AND / OR), time; ranking by the formula in 04 | 4 | DET |
| F-4 | Supersession: explicit (`supersedes`) or through a slot and gate G1; candidates, disputes, rollback — 05 §2 | 4 | DET |
| F-5 | Forgetting: archive by strength (gate G3) and purge by `retention_rules` or by an explicit request; every action is a row in `audit_log` | 4 | DET |
| F-6 | `forget(mode=archive)` hides from search with the option to restore; `forget(mode=purge)` physically deletes text, vector and links | 4 | DET |
| F-7 | Light sleep every night: decay, promoting candidates, exact duplicates, forgetting by strength, retention rules, invariant checks; report `OK` / `PARTIAL` / `FAILED` | 4 | DET |
| F-8 | Deep sleep (clean-up) once a week: finding contradictions, slot suggestions, "stale?", episode summaries; only summaries that pass gate G2 are applied automatically, the rest goes to the `review` queue | 2–3 | LLM+DET |
| F-12 | Every record has ≥ 1 piece of evidence; evidence can be added to an existing record and reinforces it | 4 | DET |
| F-13 | A record's strength is computed by the formulas of 05 §1 and used in ranking and forgetting | 4 | DET |
| F-14 | `recall`/`list` with `audience` respect the records' `visibility` (04) | 4 | DET |
| F-9 | REST and MCP use one business-logic layer; every MCP operation has a REST equivalent | 4 | DET |
| F-10 | Export of a namespace to JSONL and import from JSONL (backup, migration) | 4 | DET |
| F-11 | Every record gets its embedding synchronously on write (local model); a record without an embedding is impossible — the error goes back to the client | 4 | DET |

## Non-goals (v1)

- Extracting facts from raw text (the client does it: the agent with its own LLM, Claude Code by itself).
- An entity graph and automatic recognition of people.
- Automatically merging records into one (deep-sleep summaries are **added** on top of episodes; the sources are not hidden).
- Multi-user SaaS, sign-up, a web interface (apart from `/status`).

## Acceptance criteria (summary; details in 07)

- All 42 canaries from the check of the previous memory service, moved to the mem format, have a predictable outcome: the kind does not change, three different estimates are not merged, a meeting moved and confirmed by its author is a supersession (G1), without confirmation it is a dispute in the queue, strength does not flatten after 60 simulated nights, forgetting happens only through gate G3, purge leaves nothing behind.
- Recall@10 on a set of 20 "record — question in other words" pairs (Russian) ≥ 0.8.
- The nightly job's report with a deliberately broken stage = `FAILED` and an alert.
