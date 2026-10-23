from mem.gates import extract_dates, extract_numbers, g2, proper_names

SRC = [{"kind": "episode", "text": "15.09.2026: Игорь Дроздов прислал счёт на два дня позже, 10 досок.", "quotes": []},
       {"kind": "episode", "text": "Глеб обещал Зябликову смету до 30.09.2026.", "quotes": ["до 30.09 успею"]}]


def test_dates_formats():
    """Dates "30.09", "05.10.2026", "2026-09-15" and Russian "1 October" are extracted as (day, month) pairs."""
    assert extract_dates("до 1 октября, 30.09, 2026-09-15, 05.10.2026") == {(1, 10), (30, 9), (15, 9), (5, 10)}


def test_numbers_ignore_dates():
    """Dates are not counted as numbers, and "2,50" becomes "2.5"."""
    assert extract_numbers("30.09.2026 привёз 10 досок по 2,50") == {"10", "2.5"}


def test_g2_pass():
    """G2: a summary whose dates, numbers and names all occur in the sources passes without violations."""
    assert g2("Дроздов прислал счёт 15.09, 10 досок; смета Зябликову до 30.09.", SRC) == []


def test_g2_date_distortion():  # lesson from the previous memory service: "30.09" -> "by October 1"
    """G2a: a summary where the source date "30.09" became "by October 1" is rejected by condition a."""
    assert g2("Смета Зябликову до 1 октября.", SRC) == ["a"]


def test_g2_invented_number_and_name():
    """G2: an invented number in a summary violates condition a, an invented surname condition b."""
    assert "a" in g2("Дроздов привёз 12 досок.", SRC)
    assert "b" in g2("Иванов прислал счёт.", SRC)


def test_g2_length_kind_language():
    """G2: a summary longer than the sources, a non-episode source and a change of language violate conditions c, d and e.
    Each violation is caught separately."""
    assert "c" in g2("Дроздов " * 100, SRC)
    assert "d" in g2("Дроздов.", SRC + [{"kind": "commitment_mirror", "text": "x", "quotes": []}])
    assert "e" in g2("Drozdov was late with the invoice.", SRC)


def test_proper_names_sentence_start():
    """A function word at the start of a sentence (a preposition) is not a name, but a surname at the start of a sentence is."""
    assert proper_names("В сентябре было тихо.") == set() and "дроздов" in proper_names("Дроздов опоздал.")
