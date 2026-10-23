# 04. Search and ranking

**English** · [Русский](../ru/design/04-search.md)

## Purpose

Find the right record from a question in one's own words — in Russian and English — and rank the result in an
explainable way. The previous service's ranking was non-deterministic and wrong; here the formula is fixed, covered by
tests and visible in the response (`explain=true`).

## Algorithm [DET]

1. **Filters** (always before ranking): namespace; `state = current` (candidates only with `include_candidates`, the archive with `include_archived`); valid at `as_of` if given; `kind`, `subject`, `slot`, `tags` (`any` — intersection, `all` — containment), dates; `duplicate_of is null`; the **disclosure boundary** when `audience` is given (below).
2. **Candidates:** the union of the top 50 by vector (cosine, HNSW) and the top 50 by full-text search (`websearch_to_tsquery` for `russian` and `english`, the maximum is taken).
3. **Score** of each candidate:

```text
score = 0.50 · sem  +  0.20 · lex  +  0.20 · strength  +  0.10 · prior
  sem      = cosine similarity, normalised to [0,1] among the candidates (min-max)
  lex      = ts_rank_cd, normalised to [0,1] among the candidates; 0 without a match
  strength = the record's current strength with decay at the time of the request (05, §1)
  prior    = kinds.rank_weight × (importance / 3)
```
4. **Sorting** by `score` descending; ties by `created_at` descending, then by `id` (fully deterministic).
5. Records in the top 5 get a `recalled_top5` event (at most once a day, reinforcement 0.03 — 05). It is written after the response; an error does not affect the results.
6. For each record the response has `strength`, `evidence_count`, up to three pieces of evidence (`source_ref`, `speaker`, `observed_at`, `quote`), and `contested` with the candidate's id if the slot is disputed (05, §2).

The weights are constants in the configuration and change only together with the tests (a set of reference queries,
07).

## Disclosure boundary (`audience`)

Personal facts must not surface where they should not be heard. For the `assistant` namespace this matters in the
agent's automatic replies.

| Record's `visibility` | Visible with `audience = chat:X` if |
|---|---|
| `shareable` | always |
| `same_chat` | at least one piece of the record's evidence is from chat X |
| `owner_only` | never (only without `audience`, that is, for the owner) |

Without `audience` (the owner's briefing, `/ask`, Cowork) everything is visible. The client sets `visibility` on write;
by default the agent uses `same_chat` for facts from private chats and `shareable` for facts from work groups.

## Embeddings

- The model is chosen by a benchmark (07, M-E1) from three multilingual candidates; the criterion is separation: paraphrases of one fact should be closer than different facts with the same sentence pattern (lesson: "three different estimates" scored 0.97 with openai-small, "different facts about one person" 0.63).
- The e5 model prefixes (`query:` / `passage:`) are added by the service automatically.
- Changing the model = a migration: a new column, recomputing all vectors in batches, switching, dropping the old one.

## Functional requirements

| ID | Requirement | Co-auth. | Type |
|---|---|---|---|
| R-1 | The same query on unchanged data always gives the same order | 4 | DET |
| R-2 | `explain=true` returns `sem`, `lex`, `strength`, `prior` and the total for each record | 4 | DET |
| R-3 | The tag and namespace filter is applied before ranking, not after (no "similar" records from elsewhere) | 4 | DET |
| R-4 | An empty `query` → `list` sorted by `strength`, then `created_at` | 4 | DET |
| R-5 | With `audience` set, a record with `owner_only`, or `same_chat` from another chat, never appears in the results for any query | 4 | DET |

## Acceptance criteria

- Recall@10 ≥ 0.8 and MRR ≥ 0.6 on a reference set of 20 "record — question in other words" pairs (Russian, `assistant` namespace) and 10 pairs for `work` (mixed Russian and English).
- A query with an exact rare token (for example `EMBEDDING_MODEL`) puts the record with that token first.
- Re-running the set on unchanged data with the same `as_of` gives identical lists (a determinism test; strength depends on time, so the test fixes the moment).
- Of two equally relevant records, the one with more independent confirmations ranks higher.
- `recall(audience=chat:A)` does not return a fact with `visibility=same_chat` known only from chat B.
