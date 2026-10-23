# 05. Life cycle: memory strength, sleep, supersession, forgetting

**English** · [Русский](../ru/design/05-lifecycle.md)

## Purpose

Deliver what we expected from the previous memory service and did not get: **sleep cycles, ranking knowledge by
strength, replacing old claims with confirmed new ones, and forgetting**. The difference lies in three guarantees:

1. **Everything that changes automatically changes by a formula or rule from this document** and is covered by a test; the LLM only proposes, and proposals are applied through checkable gates.
2. **Every change is reversible** within the retention window and **backed by evidence** (quote, source, date).
3. **Disputes go to a human** (the `review_items` queue) instead of being settled silently.

## Record states

```text
                 remember (slot free / no slot)
    ────────────────────────────────────────► current ◄──── promote (gate G1) ───────┐
                                               │   ▲                                 │
    remember into a taken slot ──────────────► candidate ── no confirmation 30 days ─┤
                                               │   │                                 ▼
                     supersede (explicit/G1) ──┘   └──► superseded (history, valid_to)
                                               │
                     strength < threshold (G3) ┴──► archived ── purge rule ──► (row gone)
                                                        │  ▲
                                             unarchive  └──┘
```

## 1. Memory strength (ranking knowledge)

Every record has a **strength** `s ∈ [0,1]`: how alive the knowledge is — whether different sources confirm it,
whether it is used, how long ago it was last heard of. Strength is the basis of search ranking (04) and of forgetting
(§4). It is not the same as `importance`: the client sets importance once on write, while strength changes over time.

### Initial strength [DET]

`s₀ = {1: 0.35, 2: 0.55, 3: 0.75}[importance] + 0.10` if the first evidence is from the owner (`speaker = owner`); at
most 0.90.

### Decay [DET]

`s(t) = s(t₀) · 2^(−(t − t₀) / H)`, where `H` is the kind's half-life (02). No decay for: kinds without `H`, `pinned`,
open `commitment_mirror`. Decay is computed lazily (on read) and written down by the light sleep. **Recomputing never
raises strength** (D-6) — a direct lesson from the previous service, where a "floor of 0.35" raised everything to the
same value.

### Reinforcement [DET]

Each event: `s ← s + w · (1 − s)` (saturating towards 1, never overshooting).

| Event | w | Note |
|---|---|---|
| New **independent** evidence (a new `independent_key`: another speaker or another source) | 0.30 | The main path to "confirmation" |
| A repeat from the same source | 0.05 | A repeat ≠ a confirmation |
| Owner confirmation (`owner_confirm`) | 0.50 | A button in the bot / an MCP tool |
| The client actually used the record (`used`) | 0.15 | The agent quoted it in a briefing or answer |
| Marked helpful (`helpful`) | 0.25 | |
| Made the top 5 of the results (`recalled_top5`) | 0.03 | At most once a day per record |
| An episode went into a deep-sleep summary (`summarized_into`) | `s ← s · 0.5` | The knowledge moved into the summary; the episode fades faster |

### Examples (for tests and intuition)

| Record | Without reinforcement falls below the forgetting threshold 0.08 after |
|---|---|
| `episode`, importance 2 | ≈ 83 days |
| `note`, importance 2 | ≈ 250 days |
| `insight`, importance 2 | ≈ 335 days |
| `person_fact`, importance 2, two independent sources (s ≈ 0.69) | ≈ 3 years |
| `person_fact` mentioned by the owner and used in briefings once a month | never |

## 2. Supersession: an old claim → a confirmed new one

### Explicit supersession

The client passes `supersedes = <id>` (it is sure: "the meeting was moved"). In one transaction the old record →
`superseded`, `valid_to = now()`; the new one → `current`. That is the confirmation — the client is responsible for it.

### Supersession through a slot and confirmation

A new record in an occupied slot without `supersedes` becomes a **candidate** (`candidate`). The current record stays in
force until the candidate is confirmed.

**Repeating the same claim** (since 8 Oct): a write into an occupied slot with the same kind, subject, visibility and
classification and the same text as the current record (ignoring case, ё/е, spaces and a trailing full stop) is not a
candidate but a confirmation: the evidence is added to the current record (reinforcement, like `add_evidence`), and the
response is that record with `reinforced: true`. Otherwise at night the duplicate would replace the original (same
speaker, G1b) and the reinforcement would be lost. Only for the current record and only if the slot has no candidate
(reinforcement changes gate G1); not for rules (`decision`, `convention`, `gotcha` — they have their own confirmation
queue), not with `supersedes`, `external_ref` or promotion between layers; `same_chat` only from the same chat that
already has evidence (the audience does not widen); a late old claim (`occurred_at` earlier than the record) takes the
usual path — into history.

**Gate G1 — promoting a candidate** (light sleep, deterministic). Candidate C replaces current record K if all hold:

| # | Condition |
|---|---|
| a | C is newer: `C.occurred_at ≥ K.occurred_at` (if missing, `created_at`) |
| b | C is confirmed — at least one of: evidence from the owner; ≥ 2 independent pieces of evidence; the same speaker who gave K's original evidence (the person changed their own claim: "let's do Friday instead of Thursday") |
| c | No new evidence for K appeared after C's first evidence |

If C is older than K (a late message, an import), C goes straight to history as `superseded` with
`valid_to = K.valid_from`.

If C has not passed G1 within 14 days, a `review_item` of type `contradiction` is created with quotes from both sides.
No decision within 30 days → C is archived and the queue item becomes `expired`.

While the dispute lasts, `recall` returns the current record with `contested: true` and the candidate's id, so the
client sees there is an unverified update.

**Rollback:** `revert_supersede(<id>)` within 30 days puts the previous record back to `current` and the new one to
`candidate`. Everything goes to `audit_log`.

### Contradictions without slots

The service cannot compare records without a slot — that is the deep sleep's job (§3): the LLM looks for
contradictions within one `subject` and **creates a `review_item`** with quotes. It replaces nothing by itself. If the
contradiction is about one attribute, the proposal includes a slot: accepting it sets the slot on both records, and
gate G1 takes over from there.

## 3. Sleep cycles

### Light sleep — every night, 03:30 [DET]

| Stage | What it does |
|---|---|
| `decay` | Writes down the decayed strength of all records (`strength`, `strength_at`) |
| `dedup_exact` | Exact duplicates (normalised text: case, spaces, `ё→е`) within `namespace + kind + subject + classification + visibility + slot`: evidence moves to the kept record — the current one, otherwise the oldest (this is reinforcement); the duplicate gets `duplicate_of` and is hidden. `same_chat` records are merged only if their evidence comes from the same set of chats (otherwise the audience would widen). Since 8 Oct it runs before `promote`: a candidate duplicate with the same text does not replace the original |
| `promote` | Gate G1 for all candidates; overdue candidates → queue / archive |
| `forget` | Gate G3 (§4) |
| `retention` | `retention_rules` (purging the archive, supersession history) |
| `review_expiry` | Closes overdue queue items |
| `maintenance` | `ANALYZE`, checking invariants D-1…D-8 |
| `export` | JSONL export of namespaces for backup |

### Deep sleep ("clean-up") — once a week, Sunday 04:00

Uses an LLM (Claude through the Anthropic API — the same processor that already sees the agent's messages; embeddings
stay local). All stages except G2 **only create proposals**.

| Stage | What it does | Applied as |
|---|---|---|
| `contradictions` | For each `subject` with ≥ 2 current records without a common slot: the LLM looks for pairs that cannot both be true; the answer is a pair, an explanation and quotes from `evidence` | `review_item: contradiction` |
| `slot_suggestions` | Proposes a slot for records that describe one attribute (except `episode`, `commitment_mirror`, `insight` — those are events and summaries, not changing attributes) | `review_item: slot_suggestion` |
| `stale` | Records of a kind with `review_below` (gotcha) whose strength is below the threshold, and records referenced by "did not work" events | `review_item: stale` ("stale?") |
| `summaries` | Episodes of one `subject` older than 30 days, grouped by month → a `topic_fact`/`insight` summary | **Automatically if gate G2 passes**, otherwise `review_item: merge_suggestion` |
| `report` | The clean-up report (below) | — |

**Gate G2 — applying a summary automatically** (a deterministic check of the LLM's result):

| # | Condition |
|---|---|
| a | Every number, date and time in the summary occurs in at least one source (regex extraction, normalised date formats) — **lesson from the previous service: "30.09" cannot become "by 1 October"** |
| b | Every proper name in the summary (capitalised words other than at the start of a sentence) is in the sources (normalised `ё→е`, case endings by stem) |
| c | The summary is not longer than the sources' combined text |
| d | The sources are only `episode`; `commitment_mirror`, `decision`, `convention`, `person_fact` are never folded |
| e | The summary's language = the sources' language |

If it passes, a record is created (`source = sleep:deep`, evidence = the union of the sources' evidence), and the
sources get a `summarized_into` event. The sources are not hidden; they just fade faster.

### The clean-up report

`GET /v1/{ns}/sleep/latest` — for a human and for the agent's briefing:
- **Replaced** (old → new), each with both wordings, the ground (G1a/b/c) and quotes.
- **Forgotten** (archived by strength): counts by kind + 10 examples.
- **Stale?** — `stale` items with the quote of the latest evidence.
- **Disputes** — open `contradiction` items with quotes from both sides.
- **Summaries** — created ones and those rejected by G2 (with the reason).
- Stage statuses and errors.

### Honest report [DET]

A job's status is computed from stage errors: no errors → `OK`; an error in `contradictions`, `slot_suggestions`,
`stale`, `summaries`, `dedup_exact`, `review_expiry`, `export` → `PARTIAL`; an error in `decay`, `promote`, `forget`,
`retention`, `maintenance` → `FAILED`. `PARTIAL`/`FAILED` → an alert to Telegram. Re-running on the same data changes
nothing (idempotence). `dry_run` returns the same counters without changing anything.

## 4. Forgetting

**Gate G3 — archive by strength** (light sleep). A record is archived if all hold:

| # | Condition |
|---|---|
| a | The kind is `forgettable` |
| b | Not `pinned` |
| c | Strength after decay < 0.08 |
| d | No `used`/`helpful` events in the last 30 days |
| e | Not `current` in a slot that has an open `review_item` |

Archiving is reversible (`unarchive` sets strength to 0.30 and records an event). The clean-up report has counts and
examples.

**Purge rules** (`retention_rules`):

| id | namespace | kind | condition | days | action |
|---|---|---|---|---|---|
| r-assistant-purge | assistant | * | archived_older_than | 365 | purge |
| r-history-archive | * | * | superseded_older_than | 180 | archive (history stays in `history` until purge) |

The `work` namespace has no automatic purge.

**Explicit forgetting:** `forget(mode=archive|purge)` — one record, or in bulk by a filter with `dry_run` and
`confirm=<N>` (03).

## Functional requirements

| ID | Requirement | Co-auth. | Type |
|---|---|---|---|
| L-1 | All state transitions are functions of `mem.lifecycle`; each writes `audit_log` in the same transaction | 4 | DET |
| L-2 | Strength formulas, thresholds and weights are constants in `mem/config/lifecycle.py`, covered by property tests; a change without updating the tests fails CI | 4 | DET |
| L-3 | Gates G1, G2, G3 are pure functions with tables of test cases | 4 | DET |
| L-4 | The deep sleep's LLM stages get only record texts and quotes of one `subject`, the answer strictly by a JSON schema; an invalid answer → a stage error (`PARTIAL`), not an "empty proposal" | 3 | LLM+DET |
| L-5 | Every automatic change is reversible for at least 30 days (archive, `revert_supersede`) | 4 | DET |
| L-6 | A rule or a stage can be switched off without a deploy; a switched-off one shows in the report as `disabled` | 4 | DET |

**The sleep model (since 8 Oct).** The models are the settings `MEM_DEEP_MODEL_REASON` and `MEM_DEEP_MODEL_SUMMARY`; the
route is a prefix in the name (`mem/llm.py`, `RoutedLLM`): no prefix or `anthropic:` — Anthropic; `openai:<model>` — a
model inside your perimeter over an OpenAI-compatible protocol (`MEM_OPENAI_BASE_URL`, `MEM_OPENAI_API_KEY`), only from
the `MEM_OPENAI_IN_CONTOUR_MODELS` list. Cloud routes (`yandex:`, `gigachat:`, `openai:` outside the list) are not
supported for sleep: mem has no masking dictionary, so records would leave unmasked. Such a setting is an error before
the first call: the model stages end in error (`PARTIAL` with an alert) and nothing is sent. There is no default model:
a stage whose model is not set is switched off (`DISABLED` in the report, no alert and no call to a model), even if an
Anthropic key is configured. Anthropic is used only when named explicitly, for example
`MEM_DEEP_MODEL_REASON=claude-sonnet-5`, `MEM_DEEP_MODEL_SUMMARY=claude-haiku-4-5-20251001`.

## Acceptance criteria (details in 07)

- **Strength does not flatten:** after 60 simulated nights the 42 canaries have a spread of strength (standard deviation ≥ 0.1), and the order by strength matches the order by the number of independent confirmations.
- **Supersession with confirmation:** "call Thu 15:00" (Chizhov) → "Fri 16:00" from the same Chizhov → the light sleep promotes the candidate (G1b); from a third person without confirmation → after 14 days a `review_item` with two quotes.
- **Different things are not merged:** three estimates and a drawing for one person stay four current records; the deep sleep does not fold them (G2d, kind `commitment_mirror`).
- **G2 catches distortion:** a summary where the source date "30.09" became "1 October" is rejected with reason `a`.
- **Forgetting by strength:** an episode without reinforcement is archived after about 83 days (simulated time), a `decision` of the same age is not, a `person_fact` used in a briefing 20 days ago is not.
- **Honest report:** an error in `contradictions` → `PARTIAL` + alert; in `promote` → `FAILED`.
