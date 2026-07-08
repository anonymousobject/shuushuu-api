"""Tests for the theme tag-mapping loader (scripts/generate_tag_mappings.py).

Pure-function tests over synthetic (tag_id, title, type) rows — no DB, mirroring
tests/integration/test_generate_character_mappings.py. They pin the collision
behavior of `build_internal_tag_index`: two internal tags whose titles normalize
identically (normalize_tag strips all non-[a-z0-9\\s] chars WITHOUT inserting a
space, so punctuation just vanishes — e.g. "Re:Zero" and "ReZero" both become
"rezero") must not silently overwrite each other — the first-seen row (rows
must be pre-ordered, e.g. by tag_id) wins deterministically, and every
collision is reported via a loud warning so a human curator notices it.
"""

from scripts.generate_tag_mappings import build_internal_tag_index


def test_no_collision_all_rows_present():
    rows = [(1, "Long Hair", 1), (2, "Short Hair", 1)]
    tags = build_internal_tag_index(rows)
    assert set(tags.keys()) == {"long hair", "short hair"}
    assert tags["long hair"]["tag_id"] == 1
    assert tags["short hair"]["tag_id"] == 2


def test_collision_keeps_first_seen_row():
    # "Re:Zero" and "ReZero" both normalize to "rezero" (punctuation is
    # stripped, not replaced with a space).
    rows = [(10, "Re:Zero", 1), (20, "ReZero", 1)]
    tags = build_internal_tag_index(rows)
    assert len(tags) == 1
    assert tags["rezero"]["tag_id"] == 10
    assert tags["rezero"]["title"] == "Re:Zero"


def test_collision_is_deterministic_regardless_of_which_title_normalizes_lower():
    # Same collision, rows supplied in the opposite order -> the now-first row wins.
    rows = [(20, "ReZero", 1), (10, "Re:Zero", 1)]
    tags = build_internal_tag_index(rows)
    assert len(tags) == 1
    assert tags["rezero"]["tag_id"] == 20
    assert tags["rezero"]["title"] == "ReZero"


def test_collision_emits_warning_naming_both_titles_and_ids(capsys):
    rows = [(10, "Re:Zero", 1), (20, "ReZero", 1)]
    build_internal_tag_index(rows)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "Re:Zero" in out
    assert "ReZero" in out
    assert "10" in out
    assert "20" in out


def test_no_collision_emits_no_warning(capsys):
    rows = [(1, "Long Hair", 1), (2, "Short Hair", 1)]
    build_internal_tag_index(rows)

    out = capsys.readouterr().out
    assert "WARNING" not in out


def test_three_way_collision_lists_all_colliding_titles(capsys):
    # "A:B", "AB", and "A/B" all normalize to "ab".
    rows = [(1, "A:B", 1), (2, "AB", 1), (3, "A/B", 1)]
    tags = build_internal_tag_index(rows)

    assert len(tags) == 1
    assert tags["ab"]["tag_id"] == 1

    out = capsys.readouterr().out
    assert "A:B" in out
    assert "AB" in out
    assert "A/B" in out
