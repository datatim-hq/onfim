# Concepts

**English** · [Русский](ru/concepts.md)

This page explains how Onfim thinks about memory: what a record is, how it gains and loses strength, how a newer
claim replaces an older one, what the nightly and weekly "sleep" jobs do, and how access is decided. The REST and MCP
references ([rest-api.md](rest-api.md), [mcp.md](mcp.md)) describe the same operations call by call.

The [design specs](design/README.md) are the long form of this page; the core is `05-lifecycle.md`.

## The service never rewrites what the client sent

Onfim has no LLM on the write path. The client (your agent) decides what to remember, how to phrase it and what kind
of record it is. The service stores `text`, `kind`, `subject`, `slot`, `tags`, `importance` and the evidence as they
arrived. After that it changes only a record's **state** (current, candidate, superseded, archived) and its
**strength**, and only by the documented rules below. If a fact changes, a new record is written; the old one goes to
history.

Two things happen on write that are worth knowing:

- **Hidden characters are stripped.** Zero-width and other invisible characters, bidirectional controls, Unicode tag
  characters, ANSI escapes and control characters are removed from `text` and `quote`. What was removed is recorded
  in the record's `flags` (for example `removed:invisible:U+200B`). Mixed-script words and readable base64/hex text
  are only flagged (`flag:mixed-script:...`, `flag:encoded:base64`), not changed.
- **Secrets are refused.** A text or quote that looks like a private key or an API token (Anthropic, OpenAI, GitHub,
  GitLab, AWS, Google, Slack, Stripe, Telegram bot, JWT) is rejected with `400`. Memory is not a place for keys.

## Records

A record (a "memory") has:

| Field | Meaning |
|---|---|
| `text` | the fact, up to 4000 characters |
| `kind` | what sort of fact it is (see below); required, never changed by the service |
| `subject` | what the fact is about, free form: `project:shop`, `person:bob`, `crm:deal:42` |
| `slot` | "which attribute of the subject": `project:shop#deploy_process`, `person:bob#role`. At most one current record per slot |
| `tags` | list of strings; filters in search really filter |
| `importance` | 1, 2 (default) or 3; the client's one-time estimate, sets the initial strength |
| `visibility` | where the fact may be *said*: `shareable` (default), `same_chat`, `owner_only` |
| `classification` | who may *read* it: 0 open, 1 internal, 2 confidential, 3 strictly restricted |
| `occurred_at` | when the fact became true or the event happened (optional) |
| `external_ref` | the client's own id; a repeat write with the same `external_ref` adds evidence instead of a new record |
| `evidence` | at least one source (next section) |

The service adds `id`, `state`, `strength`, `evidence_count`, `trust`, `flags`, `valid_from`/`valid_to`, `source`
(the client id that wrote it), `pinned`, and links such as `supersedes_id`, `duplicate_of` and `promoted_from`.

### Kinds and profiles

Every namespace has a **profile** that decides which kinds it accepts. The kind sets how a record decays, whether it
can be forgotten and its weight in ranking.

| Kind | Profiles | Half-life | Forgotten by strength | Rank weight |
|---|---|---|---|---|
| `decision` — a decision taken | work, team | none | no | 1.0 |
| `convention` — a code or project convention | work, team | none | no | 0.9 |
| `gotcha` — a pitfall and its workaround | work | 180 days | no; below strength 0.25 the weekly sleep asks "still relevant?" | 0.9 |
| `note` — anything else | work | 90 days | yes | 0.5 |
| `insight` — a conclusion (also deep-sleep summaries) | work, assistant, team | 120 days | yes | 0.7 |
| `person_fact` — a fact about a person | assistant, team | 365 days | yes | 0.9 |
| `topic_fact` — context of a chat, topic or project | assistant, team | 180 days | yes | 0.8 |
| `episode` — a day's summary of a chat | assistant, team | 30 days | yes | 0.4 |
| `feedback` — the owner's correction to the agent's work | assistant | 180 days | yes | 0.8 |
| `commitment_mirror` — mirror of a commitment tracked elsewhere | assistant, team | no decay while open, 14 days after `close` | yes | 1.0 |
| `crm_mirror` — mirror of a CRM entity | assistant, team | no decay while open, 14 days after `close` | yes | 1.0 |

- **work** — development memory for coding assistants (Claude Code, Cursor and similar).
- **assistant** — a personal assistant's memory about people, chats and commitments.
- **team** — a shared team layer.

Writing a kind that the namespace's profile does not allow returns `400`. Kinds live in the `kinds` table and change
only by migration.

## Evidence

Every record must carry at least one piece of evidence: where the fact came from. A write without evidence is
rejected (`400`).

```json
{
  "source_ref": "claude-code:shop",
  "source_scope": "dev",
  "observed_at": "2026-10-09T12:00:00Z",
  "speaker": "agent",
  "quote": "redirects behind the proxy go to localhost"
}
```

| Field | Required | Meaning |
|---|---|---|
| `source_ref` | yes | a pointer to the source: a URL, a message id, `claude-code:<project>` |
| `source_scope` | yes | the kind of place it came from: `dev`, `owner`, `web`, `chat:<id>`, ... |
| `observed_at` | yes | ISO 8601 time when it was observed |
| `speaker` | no | who said it: `owner`, `agent`, `person:bob` |
| `quote` | no | a short verbatim quote, up to 200 characters |
| `speaker_principal` | no | id of a person in the organization who said it (team memory) |
| `forwarded_from` | no | the original author of a forwarded message |
| `independent_key` | no | overrides how independence is computed (advanced) |

Why it is required:

- **Explainability.** Search results, sleep reports and the review queue show who said what and when, so an agent can
  answer "according to Bob, on 22 Sept" and a human can judge a dispute.
- **Strength.** Confirmations from independent sources are what make a fact strong (next section).
- **Trust.** Whether a record is trusted is computed from its evidence, not from its text (see [Trust](#trust)).

**Independence.** Two pieces of evidence are independent when their independence keys differ. By default the key is
`p:<speaker_principal>` when a person is named, `fwd:<forwarded_from>` for forwarded messages, otherwise
`<speaker>@<source_scope>`. Evidence whose quotes are near-identical (the same text of 12+ characters, or sharing three
32-character fragments) is never counted as independent: a forward or a copy-paste is one source. A second piece of
evidence with the same `source_ref` on the same record is ignored.

## Strength and decay

Strength `s` is a number in [0, 1]: how alive a piece of knowledge is. It is separate from `importance`, which the
client sets once.

**Initial strength:** 0.35 / 0.55 / 0.75 for importance 1 / 2 / 3, plus 0.10 if the first evidence is from the owner
of a personal namespace (`speaker: "owner"`), capped at 0.90. Extra evidence sent in the same write reinforces it.

**Reinforcement:** each event moves strength towards 1 without overshooting: `s ← s + w·(1 − s)`.

| Event | w |
|---|---|
| New independent evidence | 0.30 |
| Evidence from an already-seen source | 0.05 |
| Owner confirmation (`confirm`, or accepting a review item) | 0.50 |
| `used` — the record was used in an answer or in work | 0.15 |
| `helpful` — the record turned out helpful | 0.25 |
| The record was in the top 5 of a search (at most once a day) | 0.03 |
| An episode was folded into a deep-sleep summary | `s ← s · 0.5` |

**Decay:** `s(t) = s(t₀) · 2^(−Δdays / half-life)`. Decay is computed on read and written down by the nightly sleep;
recomputing never raises strength. Pinned records, kinds without a half-life and open mirrors do not decay.

Search ranks by a fixed formula that mixes relevance and strength:

```
score = 0.50·sem + 0.20·lex + 0.20·strength + 0.10·prior
```

- `sem` — vector similarity, min-max normalised among candidates;
- `lex` — full-text rank (Postgres, english and russian), normalised; 0 without a match;
- `prior` — kind rank weight × importance / 3.

Candidates are the top 50 by vector plus the top 50 by full text, after all filters. Ties go to the newer record, then
to the id, so the same query on the same data always returns the same order. `explain=true` returns the parts.

## Slots, candidates and supersession

A slot holds at most one **current** record. A new claim replaces an old one only when it is confirmed.

**Explicit replacement.** The client passes `supersedes: <id>` of a current record. In one transaction the old record
becomes `superseded` (with `valid_to`) and the new one `current`. The client takes responsibility for it.

**Through a slot.** A new record written into an occupied slot without `supersedes` becomes a **candidate**; the current
record stays in force. The response includes `slot_current_id`. Each night the light sleep checks gate **G1**; the
candidate replaces the current record only if all of these hold:

1. the candidate is not older than the current record (`occurred_at`, else `created_at`);
2. it is confirmed: evidence from the owner, or at least two independent pieces of evidence, or the same speaker who
   gave the current record's first evidence changed their mind;
3. no new evidence for the current record arrived after the candidate's first evidence.

While a candidate waits, search returns the current record with `contested: true` and the candidate's id.

- If a candidate has not passed G1 within **14 days**, a `contradiction` item with quotes from both sides goes to the
  review queue.
- If there is still no decision after **30 days**, the candidate is archived and the item expires.
- A late arrival (a claim older than the current record) goes straight to history as `superseded`.
- **Repeating the same claim** (same kind, subject, visibility and classification, same text ignoring case, extra
  spaces and a trailing full stop) into a slot whose current record says exactly that is treated as a confirmation:
  the evidence is added to the current record and the response has `reinforced: true`. This does not apply to rule
  kinds, explicit `supersedes`, `external_ref` writes, or when a candidate is already waiting in the slot.

**Undo.** `revert-supersede` within 30 days puts the old record back to `current` and the new one back to `candidate`.
`history` shows every version of a slot (or of a replacement chain) with `valid_from`/`valid_to` and the transitions.

Records without a slot cannot be compared mechanically; the weekly deep sleep may *propose* a contradiction or a slot
for them, but never replaces anything on its own.

## The review queue

Disputed and uncertain things go to a human. Items are created by the sleep jobs and by some writes:

| Type | Created when | Accept | Reject |
|---|---|---|---|
| `contradiction` | a candidate waited 14 days, or deep sleep found a conflict | pass `winner`; the others are superseded, the winner gets an owner confirmation | nothing changes |
| `slot_suggestion` | deep sleep thinks records describe one attribute | the slot is set; the newest becomes current, older ones are superseded | nothing changes |
| `stale` | a `gotcha` fell below strength 0.25 | the record is archived | "still relevant": counts as an owner confirmation |
| `merge_suggestion` | a deep-sleep summary failed gate G2 | the summary is written as an `insight`; sources lose half their strength | nothing changes |
| `rule_confirm` | a rule (`decision`, `convention`, `gotcha`) came from an untrusted source, or from a non-curator in a team layer | the rule becomes current (replacing the slot's current record if any) | the candidate is archived |
| `promotion` | someone proposed moving a record into this layer | a new record is created here | nothing is created |

Deciding needs the `review` right. Agents should decide only on a human's direct word. Open items expire after 30 days.

## Forgetting

**Gate G3** (light sleep) archives a current record when all hold: its kind is forgettable, it is not pinned, its
strength is below **0.08**, it had no `used`/`helpful` event in 30 days, and it is not part of an open review item in its
slot. Archiving is reversible: `unarchive` restores the record with strength 0.30.

Retention rules (table `retention_rules`) handle history: by default superseded records older than 180 days are
archived, and archived records older than 365 days are purged in a namespace whose id is literally `assistant`.

Explicit forgetting: archive or purge one record, or `forget` by filter. A bulk `forget` always needs a filter, runs as
a dry run by default and applies only with `dry_run: false` and `confirm` equal to the number of matching records.
Purge is a real delete: the record, its evidence, events and vector are gone; the audit log keeps only the id.

## Sleep

Sleep jobs run in the `worker` container (see [operations.md](operations.md#the-worker-and-the-sleep-schedule)).
Each stage runs in its own transaction; a dry run rolls every stage back and returns the same counters. Running the
same job twice on the same data changes nothing the second time.

**Light sleep** — every night, deterministic, no model:

| Stage | What it does |
|---|---|
| `decay` | writes down decayed strength |
| `dedup_exact` | merges exact duplicates (same namespace, kind, subject, classification, visibility, slot and normalised text); evidence moves to the kept record, the duplicate is hidden with `duplicate_of`. `same_chat` records merge only if their chats match |
| `promote` | gate G1 for all candidates; creates `contradiction` items after 14 days, archives candidates after 30 |
| `forget` | gate G3 |
| `retention` | retention rules; purges personal layers of people who left, once their date has passed |
| `review_expiry` | expires review items open for more than 30 days |
| `maintenance` | checks invariants (one current record per slot, every superseded record has `valid_to`, every record has evidence, strength within [0, 1]) |
| `export` | writes a JSONL file per namespace if `MEM_EXPORT_DIR` is set, keeping the 14 latest |

**Deep sleep** — weekly, optionally with an LLM. The model only proposes; nothing it says is applied without a
deterministic check:

| Stage | What it does | Result |
|---|---|---|
| `contradictions` | per subject, asks the model for pairs of records that cannot both be true | `contradiction` items |
| `slot_suggestions` | asks for groups of slot-less records that describe one changing attribute (not for episodes, mirrors, insights) | `slot_suggestion` items |
| `stale` | deterministic: `gotcha` records below strength 0.25 | `stale` items |
| `summaries` | episodes of one subject older than 30 days, grouped by month, are summarised | applied automatically if gate **G2** passes, otherwise a `merge_suggestion` item |

**Gate G2** accepts a summary only if every date, time and number in it appears in the sources, every proper name
appears in the sources, it is not longer than the sources, all sources are episodes, and it is in the same language as
the sources. An accepted summary is a new `insight` record (`source: sleep:deep`) whose evidence is the union of the
sources' evidence; the sources stay, with halved strength.

What the model sees: texts and quotes of one subject in one namespace at a time. Records with `classification` 3 or
`visibility: owner_only` are never sent. There is no default model: without `MEM_DEEP_MODEL_*` the model stages are
`DISABLED` and only the deterministic `stale` stage runs.

**Honest status.** A job is `OK` only if every stage succeeded. An error in `decay`, `promote`, `forget`, `retention` or
`maintenance` makes the light sleep `FAILED`; an error in any other stage (and any deep-sleep stage) makes it `PARTIAL`.
Anything but `OK` sends an alert. A stage disabled by the operator is reported as `DISABLED`.

The **sleep report** (`sleep/latest`) shows, per namespace: what was replaced (both texts, the reason and quotes), what
was forgotten (counts by kind and up to 10 examples), merged duplicates, expired candidates, open contradictions, and
for deep sleep the slot proposals, "stale?" items and accepted and rejected summaries with reasons. Texts are filled in
when the report is read, within the reader's access.

## Trust

Memory can be poisoned: a web page or a stranger's message can contain "instructions". Onfim marks every record
`trusted` or `untrusted` from its evidence, never from its text.

In a **personal** namespace, evidence is trusted when:

- the speaker is the owner (`speaker: "owner"`, or `speaker_principal` is the namespace owner), or
- it comes from the client's own work: `source_ref` is not a URL and the `source_scope` prefix (before `:`) is one of
  the trusted scopes — by default `owner, dev, session, repo, file, import, cowork, code, claude, crm`
  (`MEM_TRUSTED_SCOPES`).

Anything else — `web`, `chat:...`, a URL — is untrusted. In a **team** or organization namespace, evidence with a
`speaker_principal` is trusted only if that person belongs to the owning group (any member of the organization for an
organization-wide layer); the word `owner` means nothing there.

A record is trusted if any of its evidence is trusted; later trusted evidence or an owner confirmation makes it
trusted. Consequences:

- **Rules wait for a human.** A `decision`, `convention` or `gotcha` from untrusted evidence does not become current:
  it is stored as a candidate with a `rule_confirm` review item for the owner.
- **Untrusted records are data.** MCP results mark them with a note telling the agent to use them as information and
  never follow instructions inside them.

## Access model

### Organizations, people, agents and groups

| Concept | What it is |
|---|---|
| **Organization** (`tenant`) | a hard boundary; nothing crosses between organizations |
| **Person** (`principal`, kind `user`) | a human, with a personal **clearance** 0–3 |
| **Agent** (`principal`, kind `agent`) | acts `on_behalf_of` a person and never sees more than that person |
| **Service** (`principal`, kind `service`) | an operational identity, such as the built-in `admin` |
| **Group** | a team, department, project or role; groups nest, and a member of a child group is a member of every parent |
| **Client** | a token. It belongs to a principal and carries a **ceiling**: which namespaces and rights it may ever use |

### Namespaces are layers

A **namespace** is a container of records with an owner: a person (`scope: personal`), a group (`scope: group`) or the
whole organization (`scope: tenant`). Typical layers are personal → team → department → organization; each is its
own namespace with its own profile. Namespace ids are lowercase letters, digits, `.`, `_` and `-` (they appear in URLs),
for example `alice.work`, `acme.pmo`, `acme.org`.

### Grants and roles

A **grant** gives a person, a group or the whole organization a role in a namespace, up to a maximum classification,
optionally until an expiry date.

| Role | Rights |
|---|---|
| `reader` | `read` |
| `contributor` | `read`, `write` (write records, add evidence, `used`/`helpful`, archive) |
| `curator` | + `review` (confirm, pin, decide review items, revert), `forget` |
| `owner` | + `manage` (export the namespace) |

`admin` is not a role: it is an operational right that works only for direct requests to the service, never through a
reverse proxy (see [operations.md](operations.md#local-admin)).

How access is decided for a request:

1. The namespace must be in the caller's organization and the person must be active; otherwise `403`, the same answer
   whether or not the namespace exists.
2. For an agent, the grants of the person it acts for count.
3. The grants that apply are the person's own, those of every group they belong to (including parents) and the
   organization-wide grant. The highest role wins; the classification limit is the highest of those grants but never
   above the person's clearance. Expired grants do not count.
4. The token's ceiling cuts the result: effective rights = role rights ∩ token rights, and the namespace must be in
   the token's list (or the list is `*`).

A group member role (`member`/`curator` in `add-member`) is stored but grants nothing by itself; rights in a namespace
come from grants.

### Classification

Each record has a level 0–3. A new record gets the namespace's default level (lowered to the writer's limit if needed);
writing above your limit is `400`. Records above the caller's limit do not exist for them: not in search, lists,
counts, history or reports, and a direct `GET` returns `404`. Reads of records at level 2 or higher are written to
`access_log` (ids only).

`visibility` is a different axis: it says where a fact may be repeated. With `audience: "chat:<id>"` in a search,
`shareable` records are returned, `same_chat` records only if some evidence came from that chat (the part after `:` in
`source_scope`, e.g. `chat:123`), and `owner_only` records never.

### Isolation in the database

The service connects as a role without the right to bypass row-level security. Each request sets the list of
namespaces it may touch, and Postgres policies hide every other row, so a bug in a query cannot open someone else's
namespace.

### Team memory

- **Delegated clients.** A team agent can use a token with `--delegate-group`. Every request must say on whose behalf
  it acts with the header `X-Mem-On-Behalf-Of: <person>` or `X-Mem-On-Behalf-Of: group:<group>` (missing header: `400`;
  a person outside the group: `403`). Acting for a group uses only the group's grants, so personal layers are
  unreachable by construction. Delegated clients work over REST only.
- **Rules in a team layer.** A `decision` or `convention` in a team namespace becomes current at once only if it was
  said by someone with `review` there (a curator); otherwise it waits as a candidate with a `rule_confirm` item.
- **Promotion between layers.** `POST .../memories/{id}/promote` proposes a copy of a current record to another
  namespace of the same organization (you need `write` in both). A curator of the target accepts or rejects it in the
  review queue; the source record never changes. Classification is never lowered. Kinds listed in the target
  namespace's `promotion_auto` setting are accepted without review.
- **Subject aliases.** `POST .../aliases` maps one subject to another for the whole organization (for example
  `person:tg123` → `crm:contact:77`); filtering by `subject` follows aliases both ways.
- **Leaving.** `offboard` deactivates a person and their agents, deletes their tokens, removes them from groups and
  schedules their personal namespaces for deletion (30 days by default). Their quotes in team memory stay as evidence.
- **Forgetting a subject.** `forget-subject` deletes every record about a subject and its aliases across the whole
  organization, with a dry run and a confirmation count.
