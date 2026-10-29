from app.services.forum_import.user_map import PosterResolution, resolve_posters


def test_forum_id_wins_and_is_rename_safe():
    # phpbb 100 links to site user 500 via forum_id, even though its phpbb name
    # ("OldName") differs from whoever holds it now — id map is authoritative.
    out = resolve_posters(
        posters={100: ("OldName", "a@x.com")},
        forum_id_map={100: 500},
        target_user_ids={500},
        target_email_to_id={"a@x.com": 999},
    )
    assert out[100] == PosterResolution(site_user_id=500, legacy_username="OldName")


def test_email_fallback_when_no_forum_id():
    out = resolve_posters(
        posters={101: ("Bob", "bob@x.com")},
        forum_id_map={},
        target_user_ids={42},
        target_email_to_id={"bob@x.com": 42},
    )
    assert out[101] == PosterResolution(site_user_id=42, legacy_username="Bob")


def test_forum_id_target_missing_falls_through_to_archived():
    # forum_id points at a user_id that doesn't exist in the target (e.g. dev
    # subset) and no email match → Archived User.
    out = resolve_posters(
        posters={102: ("Ghost", "")},
        forum_id_map={102: 700},
        target_user_ids=set(),
        target_email_to_id={},
    )
    assert out[102] == PosterResolution(site_user_id=None, legacy_username="Ghost")


def test_username_only_is_not_trusted():
    # A username-only match is deliberately NOT resolved to a real account.
    out = resolve_posters(
        posters={103: ("SharedName", "")},
        forum_id_map={},
        target_user_ids={1},
        target_email_to_id={},
    )
    assert out[103].site_user_id is None
    assert out[103].legacy_username == "SharedName"
