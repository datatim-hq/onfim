# 08. Access model: organizations, users, groups and memory layers

**English** · [Русский](../ru/design/08-access-model.md)

> v0.3, 24 Sept 2026. The goal is to build corporate memory into the architecture while there is still one user. Today
> everything runs in the owner's single "organization", but the data and the checks are already arranged so that adding
> a company, departments and employees is a matter of data and grants, not of rewriting code.

## Purpose

In corporate memory different people see different things: an employee sees their personal memory, their team's
memory and the company's shared memory; a head of department also sees the department's memory; financial and HR
information is seen only by those entitled to it. Agents (Claude Code, Cowork, a bot) work **on behalf of a person**
and never see more than that person.

## Concepts

| Concept | What it is | Example |
|---|---|---|
| **Organization** (`tenant`) | A hard boundary. Data of different organizations never meet in search, sleep or export | `demo` (personal), later `acme` |
| **Principal** (`principal`) | Whoever accesses memory: `user` — a human; `agent` — an agent on behalf of a human (`on_behalf_of`); `service` — a service process (import, sleep) | `alice`; `claude-code` → on behalf of `alice` |
| **Group** (`group`) | A team, department, role or project group; groups nest (a tree). A member of a child group is a member of every parent | `acme` → `acme/pmo` → `acme/pmo/shop-team` |
| **Memory layer** = **namespace** (`namespace`) | A container of records with an owner: a person (`personal`), a group (`group`) or the whole organization (`tenant`). Ids use dots (they are part of URLs) | `work` (alice's personal), `acme.pmo`, `acme.org` |
| **Grant** | Who (a principal, a group or the whole organization) has access to a namespace, with which role, up to which confidentiality level | group `acme/pmo` — `contributor` in `acme.pmo` up to level 1 |
| **Confidentiality level** (`classification`) of a record | 0 — open, 1 — internal, 2 — confidential, 3 — strictly restricted | a project budget — 2, employee data — 3 |
| **Disclosure boundary** (`visibility`, already exists, 04) | Where an agent may **say** the fact in a chat (the audience of a reply). It does not replace classification: classification is who may **read** it | `same_chat` |

Layers in a typical company: **personal** (every employee) → **team/project** → **department** → **organization**.
Each layer is a separate namespace. The layer defines *whose* knowledge it is and who maintains it; classification
defines *how* sensitive it is within the layer.

## Roles in a namespace

| Role | Rights | Usually |
|---|---|---|
| `reader` | `read` | neighbouring teams, observers |
| `contributor` | `read`, `write` (records, evidence, signals) | group members |
| `curator` | + `review` (confirmations, clean-up decisions, undoing supersessions), `forget` | the group's lead or knowledge owner |
| `owner` | + `manage` (grants, namespace settings) | the owner of a personal namespace; the head of a unit |

`admin` is not a role but an operational right of a token (localhost only): migrations, export, the stand.

## Access decision [DET]

A pure function `policy.resolve(principal, namespace) → (rights, max_classification)`:

1. The principal's organization = the namespace's organization, otherwise **no access** (403, no details).
2. For an agent, the person from `on_behalf_of` is taken: **their** grants count.
3. The principal's grants: direct + through every group they belong to (including nesting) + the "whole organization" grant. The resulting role is the highest; the resulting level is the highest `max_classification` among these grants, but not above the principal's personal clearance (`principals.clearance`). Expired grants (`expires_at`) do not count.
4. **Token ceiling**: the rights and namespaces written in the token are an upper bound. Effective rights = role rights ∩ token rights. So a Claude Code token can be "read/write in work only" even when its owner is `owner`.
5. No role → 403 for the whole namespace; with a role, each record is additionally filtered by `classification ≤ max_classification`.

**The filter is before ranking, in SQL** (like tags, R-3): a record above the caller's level takes no part in search,
counts, `explain`, `history` or `stats`. A direct `GET` of such a record → 404 (not 403 — its existence is not
revealed).

Writing: a new record's `classification` defaults to the namespace's level; you can write no higher than your own
clearance (otherwise 400). Only a `curator` can lower a record's level (a new record with `supersedes`, like any change
— D-1).

## Layers in search

`recall` currently works within one namespace. For the corporate scenario **search across layers** is added (stage 3+):
`POST /v1/recall` with `layers: ["personal", "group:acme/pmo", "tenant"]`, or without it — across all accessible
namespaces. Each namespace gets its own check from steps 1–5; each record in the response names its layer. The layer
weight in ranking is a parameter (by default the nearer layer ranks slightly higher: personal > team > department >
organization).

## Moving knowledge between layers ("promotion")

Personal knowledge can become team knowledge, team knowledge can become shared. This is **not a silent copy** but a
proposal to the curator of the target layer:

1. `POST /v1/{ns}/memories/{id}/promote {to: <namespace>}` — needs `write` in the source and `write` in the target.
2. A `review_item: promotion` is created in the target namespace with the text, evidence and the proposed classification.
3. The curator accepts → a new record in the target layer (`source = promote:<ns>`, link `promoted_from`), evidence is copied; quotes are copied or hidden at the curator's choice. The source record does not change.
4. Classification is not lowered automatically on promotion.

## Sleep and the LLM

- Light and deep sleep work **inside one namespace**; comparisons, summaries and duplicates never mix layers or organizations.
- A summary gets the highest classification of its sources.
- Records with `classification = 3` and `visibility = owner_only` are never sent to an external LLM (deep sleep) (S-6).
- Sleep does not look for contradictions between layers (personal says one thing, team says another); that becomes possible only through explicit promotion.

## Audit and compliance

| ID | Requirement | Type |
|---|---|---|
| AC-1 | Every action in `audit_log` has the principal and, for an agent, on whose behalf it acted | DET |
| AC-2 | Reads of records at level ≥ 2 go to `access_log`: who, when, record ids (no text) | DET |
| AC-3 | Granting and revoking grants, joining groups — in `audit_log` | DET |
| AC-4 | "Right to be forgotten": the organization's owner deletes all records about a subject (`subject`) in every namespace of the organization in one operation with `dry_run`/`confirm` | DET |
| AC-5 | Exporting a namespace — only its `owner` or `admin`; exporting an organization — only `admin` | DET |
| AC-6 | Organization isolation is duplicated at the database level (Row Level Security by `tenant_id`) — a stage before connecting a second organization | DET |

## Data model (migration 0002)

```text
tenants(id pk, name, created_at)
principals(id pk, tenant_id fk, kind user|agent|service, display_name,
           on_behalf_of fk principals null,       -- agents only
           clearance smallint 0..3, active bool, created_at)
groups(id pk, tenant_id fk, parent_id fk groups null, name, kind org_unit|team|project|role)
group_members(group_id fk, principal_id fk, role member|curator, added_at, pk(group_id, principal_id))
namespaces + tenant_id fk, scope personal|group|tenant, owner_principal null, owner_group null,
             default_classification smallint, profile work|assistant (which record kinds are allowed)
kinds.namespaces -> kinds.profiles
namespace_grants(id, namespace fk, subject_type principal|group|tenant, subject_id,
                 role reader|contributor|curator|owner, max_classification smallint,
                 granted_by, granted_at, expires_at null)
memories + classification smallint not null 0..3, promoted_from uuid null
clients (tokens) + principal_id fk principals — the token's rights and namespaces become a "ceiling"
audit_log + principal_id, on_behalf_of
access_log(id, at, principal_id, on_behalf_of, namespace, memory_ids uuid[])
review_items.type + 'promotion'
```

Migrations create no organizations, principals or namespaces. `python -m mem.cli init` creates the first organization,
the person, their personal namespace `<person>.work` with an `owner` grant up to level 3, and an agent with a token. The
service principal `admin` (organization `system`) appears at start if `MEM_ADMIN_TOKEN` is set.

## Acceptance criteria

- A member of group `acme/pmo/shop-team` reads namespace `acme.pmo` with a grant to group `acme/pmo` (inheritance up the tree); a non-member gets 403.
- An agent acting for an employee without access to `acme.finance` gets 403, even if `acme.finance` is listed in the agent's token.
- A level-2 record is not visible to a principal with clearance 1 in `recall`, `list` or `stats`; a direct `GET` → 404.
- A principal of organization `acme` gets nothing from `demo` for any request (403, the same answer for an existing and a missing namespace).
- Writing a record above one's own clearance → 400.
- A grant with an expired `expires_at` gives no access.
- All existing stage 1 tests pass with no change in behavior.

## Stages

| What | When |
|---|---|
| Migration 0002, `policy.resolve`, record classification, agent delegation, the token ceiling, CLI for organizations/groups/grants, `access_log` | **now (stage 1.5)**, before sleep |
| Search across layers (`POST /v1/recall` over several namespaces) | stage 3 (together with connecting clients) |
| Promotion between layers, `review_item: promotion` | after stage 4 (needs the queue and its handling) |
| RLS in Postgres, "right to be forgotten" across an organization | before connecting a second organization |
