# 09. Team memory: a shared layer, personal layers, delegation

**English** · [Русский](../ru/design/09-team-memory.md)

> v0.1, 2 Oct 2026. Develops the access model (08) into a working team scenario. The owner's decisions of 2 Oct: build
> full team work and test it on a fictional sales department (organization `globex` in
> `tests/integration/test_team.py`).

## Purpose

A team uses one agent. The team has shared memory, and each member has personal memory. When the agent answers a
person, it sees exactly what that person is entitled to see. Only what was said in team chats, and what a person
shared themselves, gets into the shared memory.

The access model (08) already provides organizations, groups, roles, confidentiality levels and search across layers.
This spec adds what is missing: delegation to an agent service, the speaker as a principal, trust in a team, moving
between layers, routing of the review queue, a member leaving, and an external entity key for CRM.

## Concepts

| Concept | What it is | Example |
|---|---|---|
| **Team** | A group (`group`) with a team namespace (`scope = group`, profile `team`) | group `gx/sales`, namespace `gx.sales` |
| **Personal layer** | A member's personal namespace (`scope = personal`, profile `assistant`) | `gx.u.bob` |
| **Curator** | A member with the `curator` role in the team namespace: decides on the team's queue | the head of the department |
| **Delegated client** | The team agent's service token. It has no access to records by itself; it acts on behalf of a group member or of the group itself | client `gx-agent` delegated to `gx/sales` |
| **Speaker** | A member of the organization whose words became evidence (`evidence.speaker_principal`) | `bob` |
| **External party** | Someone who writes in a chat but is not in the organization: a customer, a supplier's representative | — |

## Scenarios

- **TM-S1. A question in a private chat with the agent.** The agent searches across layers on behalf of the employee: the personal layer and the team layer, with personal weighing slightly more.
- **TM-S2. A question in a team chat.** The agent searches on behalf of the **group**. Personal layers are absent from the results by construction: the group has no grants on them.
- **TM-S3. "Share with the team."** An employee sends a personal record to the team layer. The curator gets a proposal with the text and evidence and accepts or rejects it. The source record does not change.
- **TM-S4. Two independent confirmations.** Two managers report the same thing about a property at different times. These are two independent sources: the candidate replaces the outdated record in the light sleep (gate G1, 05).
- **TM-S5. A team rule.** A member writes in the team chat "we hold a reservation for no more than three days". A record of the "rule" kind becomes a candidate and waits for the curator. If the curator said it, it applies at once.
- **TM-S6. An external party.** A supplier's representative writes in a mixed chat "prices for the third building go up on 10 October". The record is stored as untrusted: the agent presents it as "according to …", and it never becomes a rule.
- **TM-S7. A manager and personal data.** A curator requests an employee's personal namespace — refused with 403, as for a namespace that does not exist.
- **TM-S8. A dispute in the team.** Two members claim different things in one slot. A "contradiction" item with both quotes goes to the curator.
- **TM-S9. A member leaves.** The member is deactivated: all their tokens and any delegation on their behalf stop working; the personal layer is exported to them and erased; their words in team memory remain as evidence.
- **TM-S10. Information from a CRM.** A record about a deal is stored with an external key `crm:deal:…`; a search by a person from a chat finds it too if the person is linked to a CRM contact.

## Functional requirements

### Delegation

| ID | Requirement | Type |
|---|---|---|
| TM-1 | A client gets an optional field `delegate_group`. Such a client must state on whose behalf it acts in every request: the header `X-Mem-On-Behalf-Of` with the value `<participant_id>` or `group:<group_id>`. Without the header — 400 | DET |
| TM-2 | Acting on behalf of a member is allowed only if the member is active and belongs to `delegate_group` or one of its child groups. Otherwise — 403 | DET |
| TM-3 | Request rights = that member's rights (08, steps 1–5) ∩ the token ceiling. A delegated client gets nothing beyond the person's rights | DET |
| TM-4 | On behalf of a group (`group:<id>`), only the grants of the group itself, its parent groups and "whole organization" grants count. Personal namespaces are unreachable by construction | DET |
| TM-5 | `audit_log` and `access_log` record the client and on whose behalf it acted (AC-1) | DET |
| TM-6 | Delegation is not used over MCP: an MCP client has one person per token, as before | DET |

### Speaker and trust

| ID | Requirement | Type |
|---|---|---|
| TM-7 | Evidence gets an optional field `speaker_principal`. The principal must exist in the namespace's organization, otherwise 400. The `speaker` field stays for display | DET |
| TM-8 | The evidence independence key: `p:<speaker_principal>` if the speaker is given; otherwise the previous rule (`who said it@source`). A forwarded message still counts as one source (1.6) | DET |
| TM-9 | **Trust in a personal layer** — as before: the words of the layer's owner and the trusted client's own work. The owner is determined as: `speaker_principal` equals the namespace owner, or `speaker = owner` | DET |
| TM-10 | **Trust in a team layer:** evidence is trusted if it comes from a member of the owning group. From an external party or external sources it is untrusted | DET |
| TM-11 | **Team rules** (kinds `decision`, `convention`) in a team layer apply at once only if the speaker is a curator. From other members the record becomes a candidate and a `rule_confirm` item is created for the curator | DET |
| TM-12 | The `team` namespace profile allows the kinds: `topic_fact`, `person_fact`, `insight`, `episode`, `commitment_mirror`, `decision`, `convention`, `crm_mirror` | DET |

### Moving between layers

| ID | Requirement | Type |
|---|---|---|
| TM-13 | `POST /v1/{ns}/memories/{id}/promote` with the body `{to, share_quotes?, note?}`. Needs `write` in the source and in the target namespace. Both in one organization | DET |
| TM-14 | A `promotion` queue item is created in the target namespace: text, kind, subject, slot, evidence, who proposed it, the proposed confidentiality level. With `share_quotes = false`, quotes are not included in the item | DET |
| TM-15 | The curator accepts → a new record is created in the target layer: `source = promote:<source namespace>`, `promoted_from = <id>`, evidence copied with the same independence keys. If the slot is taken, the record becomes a candidate under the usual rules (05) | DET |
| TM-16 | The curator rejects → nothing appears in the target layer. The source record does not change in either outcome | DET |
| TM-17 | The confidentiality level is not lowered automatically on promotion (08) | DET |
| TM-18 | Promoting the same record to the same namespace again while an item is open — 409. After a rejection a repeat is allowed | DET |
| TM-19 | Namespace setting `promotion_auto`: a list of kinds accepted without a curator. Empty by default | DET |

### The review queue in a team

| ID | Requirement | Type |
|---|---|---|
| TM-20 | Items of a team namespace are seen and decided by members with the `review` right there, that is, curators. Items of a personal namespace — only by its owner | DET |
| TM-21 | `GET /v1/review` without a namespace returns open items in every namespace where the caller has the `review` right, with the layer named | DET |
| TM-22 | `promotion` and `rule_confirm` items name who proposed them (`proposed_by`) | DET |

### Search across layers

| ID | Requirement | Type |
|---|---|---|
| TM-23 | Search across layers (`recall_layers`) takes `scopes`: a subset of `personal`, `group`, `tenant`. Without it — all accessible | DET |
| TM-24 | To answer in a team chat, the client must search on behalf of the group (TM-4). The `scopes` parameter is a convenience, not protection | DET |
| TM-25 | Each record in the response names its layer: the namespace and its type (already done) | DET |

### A member leaving and "forget a subject"

| ID | Requirement | Type |
|---|---|---|
| TM-26 | `offboard <participant>`: the member becomes inactive, their clients are revoked, they are removed from groups. From then on any request on their behalf — 403 | DET |
| TM-27 | The leaver's personal layer: exported (owner's right or `admin`), then deleted after a period — 30 days by default, immediately if needed. Until deletion the layer is accessible to no one | DET |
| TM-28 | The leaver's words in team memory remain as evidence with their name. Their candidate records in the curator's queue remain | DET |
| TM-29 | "Forget a subject": an organization-level operation deletes every record with the given `subject` and its aliases in all namespaces of the organization. `dry_run` and `confirm = <number of records>` modes, as in `forget` (03). Right: the organization's owner or `admin` | DET |

### External entity key

| ID | Requirement | Type |
|---|---|---|
| TM-30 | A record's subject (`subject`) can be an external key: `crm:contact:<id>`, `crm:deal:<id>`, `crm:object:<id>` | DET |
| TM-31 | A table of subject aliases per organization: `person:tg123 → crm:contact:77`. Filtering by `subject` in search and lists follows aliases both ways | DET |
| TM-32 | Kind `crm_mirror`: a mirror record of a CRM entity with `external_ref`. While the entity is open, strength does not decay; after closing (`/close`) it decays and is forgotten like `commitment_mirror` | DET |
| TM-33 | Evidence with `source_scope = crm` is not considered untrusted, but it does not serve as an independent confirmation of chats if the CRM record itself was created by the agent (the independence key `crm:<entity>` is one per entity) | DET |

### Sleep

| ID | Requirement | Type |
|---|---|---|
| TM-34 | Light and deep sleep still run inside one namespace and do not mix layers (08) | DET |
| TM-35 | Deep sleep of personal namespaces is spread over time; namespace setting `deep_sleep: weekly | off` | DET |

## Data model (migration 0005)

```text
clients         + delegate_group text null → groups(id)
evidence        + speaker_principal text null → principals(id)
namespaces      + settings jsonb not null default '{}'     -- promotion_auto, deep_sleep
kinds             profile team; a new kind crm_mirror (half-life like commitment_mirror)
review_items    + proposed_by text null → principals(id)
subject_aliases   (tenant_id, alias, canonical, created_by, created_at; pk (tenant_id, alias))
```

Existing clients and records do not change: all new fields are optional, and behavior without them stays the same.

## Acceptance criteria

The isolation matrix is checked on organization `globex` (five members, group `gx/sales`, a curator):

- Employee A, through a delegated client, reads their personal layer and the team layer; B's personal layer — 403.
- The curator reads the team layer and decides on its queue; any employee's personal layer — 403.
- A request on behalf of `group:gx/sales` returns only the team layer, even if the request names a personal namespace (403).
- A delegated client without the header — 400; on behalf of a member of another group or another organization — 403; on behalf of an inactive member — 403.
- Promotion: accepted → a record in the team layer with `promoted_from`; rejected → no record; the source record unchanged in both cases; a repeat while the item is open — 409.
- Two pieces of evidence from different members are two independent sources; two from one member in different chats are one.
- A rule from a member is a candidate plus a `rule_confirm` item; from a curator it applies at once.
- An external party's words in the team layer are `trust = untrusted`.
- After `offboard`, a request on behalf of the leaver — 403; their words in the team layer are visible.
- A search by `subject = person:tg123` finds a record with subject `crm:contact:77` when an alias exists.
- All previous tests pass unchanged.

## Implementation status

Implemented in migration 0005; tests in `tests/integration/test_team.py`.

| Requirements | Status |
|---|---|
| TM-1…TM-5 delegation | ✅ header `X-Mem-On-Behalf-Of`, on behalf of a member and of a group |
| TM-6 | ✅ a delegated client without the header gets 400, so it does not work over MCP |
| TM-7…TM-12 speaker, trust, team rules, profile `team` | ✅ |
| TM-13…TM-19 moving between layers | ✅ `POST /v1/{ns}/memories/{id}/promote`, decided through the usual queue |
| TM-20…TM-22 queue | ✅ `GET /v1/review` across all accessible layers |
| TM-23…TM-25 layers in search | ✅ parameter `scopes` |
| TM-26…TM-28 a member leaving | ✅ `mem.cli offboard`, the personal layer deleted after its period by the light sleep; `purge-namespace` by hand |
| TM-29 "forget a subject" | ✅ `mem.cli forget-subject` (server only) |
| TM-30…TM-32 external key, aliases, `crm_mirror` | ✅ `POST /v1/{ns}/aliases` |
| TM-33 confirmation loop | The independence key is set by the client (the CRM gateway) — checked at stage 5 |
| TM-34, TM-35 sleep | ⏳ spreading the deep sleep over time — when the number of namespaces grows |
| MCP tools for promotion and aliases | ⏳ not done: the team agent works over REST |

## Stages

| What | When |
|---|---|
| TM-1…TM-12, TM-20…TM-25 — delegation, speaker, trust, queue, layers | Stage 1 of the team roadmap |
| TM-13…TM-19 — moving between layers | Stage 1 |
| TM-26…TM-29 — a member leaving, "forget a subject" | Stage 1, before the pilot |
| TM-30…TM-33 — external key, aliases, `crm_mirror` | Before stage 5 (the CRM gateway) |
| TM-34, TM-35 — sleep | As the number of namespaces grows |

## Lessons from production

_None yet._
