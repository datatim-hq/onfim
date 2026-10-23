# M-E1: choosing the embedding model (24 Sept 2026)

**English** · [Русский](../ru/research/embed-benchmark.md)

Run: `tools/embed_bench.py` on a 4 vCPU server, 42 canaries from `tests/fixtures/canaries.json`, 24 paraphrased
questions (dense search only, no full text and no strength).

| Model | dim | background (all pairs) | paraphrases c301–c303, σ | "three estimates" c701–c703, σ | facts about a person c501–c503, σ | R@10 | MRR | ms/text (CPU) |
|---|---|---|---|---|---|---|---|---|
| **intfloat/multilingual-e5-small** | 384 | 0.846 | 0.971 (3.98) | 0.983 (4.37) | 0.921 (2.38) | **1.0** | **0.972** | 11 |
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.33 | 0.703 (2.51) | 0.88 (3.70) | 0.452 (0.82) | 0.958 | 0.819 | 12 |
| intfloat/multilingual-e5-base | 768 | 0.836 | 0.971 (4.11) | 0.989 (4.66) | 0.902 (2.01) | 1.0 | 0.951 | 29 |

σ is how far a group's similarity is above the background of all canary pairs, in standard deviations (raw e5 cosines
are squeezed into 0.8–1.0, so they cannot be compared with MiniLM directly).

The canaries and questions are in Russian: the default model had to handle Russian well.

## Decision

**`intfloat/multilingual-e5-small`, 384.** The best MRR, full Recall@10, 2.5 times faster and three times lighter than
e5-base, and the strongest at linking different facts about one person.

## What the "gap" criterion from 07 showed

No model ranks "three different estimates" below paraphrases of one fact: texts c701–c703 differ by a single word, and
for any embedding model they are closer than paraphrases are. This confirms the conclusion of an earlier experiment:
**vector similarity cannot protect different commitments from being merged.** In Onfim this protection is structural,
not vector-based:

- the service never merges records by similarity; only exact duplicates are merged (`dedup_exact`, normalised text);
- `commitment_mirror`, `decision`, `convention`, `person_fact` are never folded into summaries (G2d);
- anything disputed goes through slots and the owner's review queue.

So the M-E1 criterion was refined: the model is chosen by Recall@10 ≥ 0.8 and MRR on the reference questions; the "gap"
is a reference metric.
