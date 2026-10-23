from mem import search as S


def c(id, sem, lex=0.0, strength=0.5, rw=0.8, imp=2, ts=0.0):
    return S.Candidate(id, sem, lex, strength, rw, imp, ts)


def test_weights_sum_to_one():
    """The weights of the ranking formula (meaning, words, strength, prior) sum to 1."""
    assert abs(S.W_SEM + S.W_LEX + S.W_STRENGTH + S.W_PRIOR - 1.0) < 1e-9


def test_deterministic_and_tiebreak():  # R-1
    """R-1: result order does not depend on candidate order; with an equal score the newer record wins, then the smaller id."""
    cands = [c("b", 0.5, ts=1), c("a", 0.5, ts=1), c("c", 0.5, ts=2)]
    r1 = [x["id"] for x in S.score(cands)]
    r2 = [x["id"] for x in S.score(list(reversed(cands)))]
    assert r1 == r2 == ["c", "a", "b"]


def test_strength_breaks_equal_relevance():  # 04 acceptance: more confirmations rank higher
    """04: with equal relevance the record with more strength, that is more confirmations, ranks higher."""
    r = S.score([c("weak", 0.7, strength=0.55), c("strong", 0.7, strength=0.84)])
    assert [x["id"] for x in r] == ["strong", "weak"]


def test_lex_zero_when_no_match():
    """Without a word match lex is 0 even with high semantic similarity; the only match gives 1."""
    r = {x["id"]: x for x in S.score([c("a", 0.9, lex=0.0), c("b", 0.1, lex=0.3)])}
    assert r["a"]["lex"] == 0 and r["b"]["lex"] == 1.0


def test_explain_fields():  # R-2
    """R-2: the score breakdown has id, total and the components sem, lex, strength, prior, and nothing else."""
    x = S.score([c("a", 0.4, 0.1)])[0]
    assert set(x) == {"id", "score", "sem", "lex", "strength", "prior"}
