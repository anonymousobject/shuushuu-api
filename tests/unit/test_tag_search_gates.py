"""Gates decide which candidate branches a query runs (spec: Candidate set)."""

import pytest

from app.services.tag_search import SearchGates, gates_for


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Under 3 chars, or no 3-char alphanumeric run: prefix-only path.
        ("sa", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        ("C++", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        ("C.C.", SearchGates(prefix_only=True, fuzzy=False, secondary=False)),
        # "the" is 3 chars but has only 3 letters: no fuzzy; "f" is 1 char: no desc/URL.
        ("the f", SearchGates(prefix_only=False, fuzzy=False, secondary=False)),
        # Digits form the run; no letters at all: no fuzzy, but desc/URL run.
        ("100%", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        # CJK ideographs are letters and alphanumerics.
        ("EB十", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        ("neko", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
        ("sakura", SearchGates(prefix_only=False, fuzzy=True, secondary=True)),
        ("sakrua kinomto", SearchGates(prefix_only=False, fuzzy=True, secondary=True)),
        # Five letters (the o inside 0o0 counts), so the fuzzy branch runs.
        ("yano_0o0", SearchGates(prefix_only=False, fuzzy=True, secondary=True)),
        ("21412050", SearchGates(prefix_only=False, fuzzy=False, secondary=True)),
    ],
)
def test_gates_for(query: str, expected: SearchGates):
    assert gates_for(query, query.split()) == expected
