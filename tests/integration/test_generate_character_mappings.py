"""Tests for the character tag-mapping matcher (scripts/generate_character_mappings.py).

Pure-function tests over a small synthetic internal-tag set — no DB. They pin the
match tiers: exact, qualifier-stripped exact, name-order swap, ambiguous (one
Danbooru name -> several internal tags), fuzzy (romanization/typo), and no-match.
"""

from pathlib import Path

from app.config import settings
from scripts.generate_character_mappings import (
    DEFAULT_VOCAB,
    MatchResult,
    apply_linked_only,
    build_internal_index,
    classify,
    match_all,
    strip_qualifier,
)


def _mr(danbooru: str, iid: int, action: str = "map", match_type: str = "exact") -> MatchResult:
    return MatchResult(
        danbooru_tag=danbooru,
        internal_tag_id=str(iid) if iid else "",
        internal_tag_title=f"Tag{iid}",
        match_type=match_type,
        score=100,
        candidates="",
        action=action,
    )

# (tag_id, title)
INTERNAL = [
    (82250, "Kinomoto Sakura"),
    (100, "Hatsune Miku"),
    (300, "Chen"),
    (301, "Chen"),  # duplicate title -> ambiguous
]


def _index():
    return build_internal_index(INTERNAL)


def test_default_vocab_tracks_configured_model():
    """DEFAULT_VOCAB must point at the currently-configured model's vocab, not a
    hardcoded model name — otherwise a --vocab-less run silently uses the wrong
    vocabulary once the production model changes (see settings.ML_MODEL_NAME)."""
    assert DEFAULT_VOCAB == Path(f"ml_models/{settings.ML_MODEL_NAME}/selected_tags.csv")


def test_strip_qualifier():
    assert strip_qualifier("tomoyo_(cardcaptor_sakura)") == "tomoyo"
    assert strip_qualifier("kinomoto_sakura") == "kinomoto_sakura"
    assert strip_qualifier("d.va_(overwatch)") == "d.va"


def test_exact_full_name():
    r = classify("hatsune_miku", _index())
    assert r.action == "map"
    assert r.match_type == "exact"
    assert r.internal_tag_id == "100"
    assert r.internal_tag_title == "Hatsune Miku"
    assert r.score == 100


def test_exact_after_stripping_qualifier():
    r = classify("kinomoto_sakura_(cardcaptor_sakura)", _index())
    assert r.action == "map"
    assert r.match_type == "exact_stripped"
    assert r.internal_tag_id == "82250"


def test_name_order_swap():
    # danbooru "miku_hatsune" -> "miku hatsune"; sorted tokens match "Hatsune Miku"
    r = classify("miku_hatsune", _index())
    assert r.action == "map"
    assert r.match_type == "swap"
    assert r.internal_tag_id == "100"


def test_ambiguous_lists_candidates_and_blanks_id():
    r = classify("chen", _index())
    assert r.action == "review"
    assert r.match_type == "ambiguous"
    assert r.internal_tag_id == ""  # forces a human pick
    assert "300" in r.candidates and "301" in r.candidates


def test_fuzzy_typo_proposes_candidate_for_review():
    r = classify("hatsune_miiku", _index())  # one-letter typo
    assert r.action == "review"
    assert r.match_type == "fuzzy"
    assert r.internal_tag_id == "100"
    assert r.score >= 88


def test_no_match_is_ignored():
    r = classify("qwxyzzz_nonexistent", _index())
    assert r.action == "ignore"
    assert r.match_type == "none"
    assert r.internal_tag_id == ""


def test_fuzzy_does_not_grab_short_tag_for_long_name():
    """A length-sensitive scorer must NOT fuzzy-match a long Danbooru name to a tiny
    internal tag (the WRatio partial-match failure mode: '35p' -> 'P')."""
    index = build_internal_index(INTERNAL + [(99, "P"), (98, "Ai"), (97, "Art")])
    for name in ("35p_(sakura_miko)", "airani_iofifteen", "adeptus_astartes"):
        r = classify(name, index)
        assert r.match_type == "none", f"{name} spuriously matched {r.internal_tag_title!r}"


def test_match_all_counts():
    names = ["hatsune_miku", "chen", "qwxyzzz_nonexistent"]
    results = match_all(names, INTERNAL)
    by_action = {}
    for r in results:
        by_action[r.action] = by_action.get(r.action, 0) + 1
    assert by_action == {"map": 1, "review": 1, "ignore": 1}


def test_linked_only_keeps_linked_unique_demotes_unlinked():
    res = [_mr("a", 10), _mr("b", 11)]  # 10 is source-linked, 11 is not
    out = {r.danbooru_tag: r for r in apply_linked_only(res, {10})}
    assert out["a"].action == "map"
    assert out["b"].action == "review"  # unlinked internal -> not launchable


def test_linked_only_demotes_merge_collisions_even_if_linked():
    # both Danbooru names map to the same linked internal (10) -> merge collision
    res = [_mr("a", 10), _mr("c", 10)]
    out = apply_linked_only(res, {10})
    assert all(r.action == "review" for r in out)


def test_linked_only_leaves_review_and_ignore_untouched():
    res = [_mr("x", 0, action="review", match_type="fuzzy"),
           _mr("y", 0, action="ignore", match_type="none")]
    out = apply_linked_only(res, {10})
    assert [r.action for r in out] == ["review", "ignore"]
