# 07. Verification and acceptance

**English** · [Русский](../ru/design/07-verification.md)

## Purpose

Check mem with the same method we used to check the previous memory service: canaries with a pre-recorded outcome,
"before/after" snapshots, and no trust in the system's own reports. The acceptance criteria of specs 00–06 are written
as automated tests wherever possible.

## Levels of verification

| Level | What | Where |
|---|---|---|
| Unit | Service layer, rules, the ranking formula, duplicate normalisation | `tests/unit`, CI |
| Integration | Postgres + the model in a container: REST and MCP, migrations, invariants D-1…D-6 | `tests/integration`, CI (docker compose) |
| Canaries | 42 canaries from the check of the previous service, moved to the mem format (`tools/memlab.py`) | the stand, before switching clients over |
| Reference search | 30 "record — question" pairs (20 assistant, 10 work) | CI + stand |
| Monitoring | daily in production | a monitoring script |

## Canary test cases

Time is simulated: the test stand can shift the service's "current time" (`MEM_CLOCK_OFFSET`, stand mode only), so
decay, candidate promotion and forgetting are checked in minutes rather than months. Lesson from the earlier check:
without this, consolidation had to be "aged" with SQL queries.

### Kinds and evidence

| ID | Check | "Passed" criterion | In the previous service |
|---|---|---|---|
| M-L1 | The kind does not change | 42/42 `kind` match what was written after 10 min, after the light and the deep sleep | V-L1 ❌ |
| M-L2 | There is no classifier | A record without `kind` or without `evidence` → 400 | V-L2 ❌ |
| M-E0 | Evidence in the results | `recall` for "when is Chizhov available" returns the record and a quote with date and speaker | patterns without sources |

### Strength (ranking knowledge)

| ID | Check | Criterion |
|---|---|---|
| M-S1 | Does not flatten | After 60 simulated nights: the standard deviation of the 42 canaries' strength ≥ 0.1; no record grew without an event |
| M-S2 | Reinforcement works | A record with 3 independent pieces of evidence is stronger than the same record with 1 (and ranks higher at equal relevance) |
| M-S3 | A repeat ≠ a confirmation | 5 identical pieces of evidence from one source give less gain than one independent one |
| M-S4 | Use keeps it alive | c901 (`topic_fact`) with `used` every 20 days keeps strength > 0.5 for the whole simulated year; c903 without events drops below 0.2 (the forgetting threshold is about 500 days away for `topic_fact`, about 1000 for `person_fact`: a fact about a person is not forgotten within a year, and that is right) |
| M-S5 | No "noise elite" | `assistant` has no records the service created by itself outside the deep sleep; no record has strength > 0.9 without ≥ 3 reinforcement events |

### Supersession

| ID | Check | Criterion | In the previous service |
|---|---|---|---|
| M-C2a | The author changed it | c401 "Thu 15:00" (evidence: Chizhov) → c402 "Fri 16:00" in the same slot, evidence: Chizhov → after the light sleep c402 is `current`, c401 `superseded` with `valid_to`; the clean-up report has both quotes | V-C2 — not checked |
| M-C2b | A third person, no confirmation | the same, but c402 according to Sinitsyn → c402 stays `candidate`, `recall` returns c401 with `contested: true`; after 14 simulated days a `review_item` with two quotes | — |
| M-C2c | A second independent source | evidence from Ivolgina added to c402 → promotion (G1b) | — |
| M-C2d | Rollback | `revert-supersede` → c401 `current` again, c402 `candidate` | — |
| M-C2e | A late old claim | a write into the slot with `occurred_at` earlier than the current record → straight to `superseded`, the current record untouched | — |
| M-C7 | Different things are not merged | c701–c703 and c107 are four current records after the deep sleep; no summary is made of them (G2d) | V-C7 ❌ |
| M-C1 | Exact duplicates | a repeat of c301 → evidence moved to c301 (strength up by 0.05), the duplicate hidden | — |
| M-C6 | A contradiction without a slot | "Shcheglova answers only in writing" and "Shcheglova prefers calls" → the deep sleep creates a `review_item: contradiction` with two quotes and a slot proposal; nothing is replaced | — |

### Sleep

| ID | Check | Criterion | In the previous service |
|---|---|---|---|
| M-D1 | An episode summary passes G2 | episodes c601–c604 (4 simulated months ago) → a summary in Russian, all dates from the sources, the sources with a `summarized_into` event | V-C6 ⚠️ (in English, without sources) |
| M-D2 | G2 catches distortion | a substituted LLM answer with "1 October" instead of "30.09" → the summary is rejected, reason `a`, `review_item: merge_suggestion` | consolidation → archive chain |
| M-D3 | An invalid LLM answer | the mock returns non-JSON → the stage errors, the run is `PARTIAL`, an alert | — |
| M-J1 | Honest report | an error in `promote` → `FAILED` + alert | V-S2 ❌ |
| M-J2 | Idempotence | a second light sleep in a row — all `changed = 0` | V-S3 ❌ |
| M-J3 | Scheduled nightly run | 3 nights in a row the light sleep runs at 03:30 ± 5 min; the deep one on Sunday | V-S1 ✅ |

### Forgetting and deletion

| ID | Check | Criterion | In the previous service |
|---|---|---|---|
| M-F2 | Forgetting by strength | an `episode` without reinforcement → archived on about simulated day 83 (± 1); a `decision` — never; `pinned` — never | V-F2 ❌ (all or nothing) |
| M-F3 | Restore | `unarchive` → back in search, strength 0.30, not archived again until its strength falls below the threshold again | V-F3 ⚠️ |
| M-F1 | Purge | after `DELETE ?mode=purge` there are no rows in `memories`, `evidence`, `memory_events`; `audit_log` has no text or quotes | V-F1 ⚠️ |
| M-F4 | Bulk deletion is safe | `forget(subject=…)` without `confirm` deletes nothing | a lesson from another service |

### Access and disclosure

| ID | Check | Criterion |
|---|---|---|
| M-A1 | Isolation | the `cowork` token → `assistant`: 403 |
| M-A2 | The `review` right | the `claude-code` token on `/confirm` → 403 |
| M-A3 | Disclosure boundary | a fact with `visibility=same_chat` from the chat "Nest" is not returned with `audience=chat:dacha` for any query |
| M-T1 | Tags over MCP | `recall(tags=[canary])` over MCP returns only canaries |

### Embedding model

| ID | Check | Criterion |
|---|---|---|
| M-E1 | Choosing the model | for three models, a table of similarity by canary group; the model with Recall@10 ≥ 0.8 and the highest MRR on the reference questions is chosen. The paraphrase/pattern "gap" is for reference: no model separates the "three estimates" (24.09, `docs/research/embed-benchmark.md`), so the protection from merging is structural. **Chosen: `intfloat/multilingual-e5-small` (384)** |

## Monitoring in operation

`/status/<secret>` returns: `memories` by namespace and state, the strength distribution (quartiles), `lastLightSleep`
and `lastDeepSleep` (time, status), the number of open `review` items, `embedDim`, `modelId`, `dbOk`. Sensors (added to
the server's monitoring script and to the assistant's task):

| Sensor | Threshold |
|---|---|
| `lastLightSleep.status` / age | `OK` / ≤ 30 h |
| `lastDeepSleep.status` / age | `OK` / ≤ 8 days |
| open `review` items older than 14 days | 0 (otherwise a reminder in the briefing) |
| median strength | not equal to the 1st and 3rd quartiles (a sign of "flattening", as in the previous service) |
| `dbOk` | true |
| `/status` response | ≤ 2 s |
| mem backup | ≤ 30 h |

## Pilot completion criteria

- Every case in the tables has "passed".
- Reference search: Recall@10 ≥ 0.8, MRR ≥ 0.6.
- 7 days in production without `FAILED` and without "it was not found" complaints.

## Implementation of the checks (24 Sept 2026)

Cases M-L*, M-E0, M-S1…S5, M-C1, M-C2a…e, M-C7 (light sleep), M-F1…F4, M-A1…A3, M-T1, M-J1, M-J2 are automated tests in
`tests/integration/test_api.py`, `test_sleep.py`, `test_access.py` (stand `mem-test`, time shifted by the service
clock). Deep sleep (stage 4): M-C6, M-D1, M-D2, M-D3, M-C7 (deep), S-6 — `tests/integration/test_deep_sleep.py` with a
substituted LLM; gate G2 — `tests/unit/test_gates.py`. Not automated: M-J3 (the schedule — observed over nights in
production).

**A run on a real model (24 Sept 2026, stand, claude-sonnet-5 / claude-haiku-4-5):** the "Shcheglova: in writing /
calls" contradiction was found with a correct explanation and quotes; the three estimates and the drawing were neither
folded nor declared a contradiction; the summary of Drozdov's four episodes passed G2 without distorting dates. False
positive: the model proposed a slot for episodes ("delivery delay") — episodes, commitment mirrors and summaries are now
excluded from slot proposals. On working memory (19 records): 0 false contradictions and slots.

## Lessons from production

_None yet._
