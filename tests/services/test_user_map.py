from app.services.forum_import.user_map import PosterResolution, resolve_posters


def test_forum_id_wins_and_is_rename_safe():
    # phpbb 100 links to site user 500 via forum_id, even though its phpbb name
    # ("OldName") differs from whoever holds it now — id map is authoritative.
    out = resolve_posters(
        posters={100: ("OldName", "a@x.com")},
        forum_id_map={100: 500},
        target_user_ids={500},
        target_accounts_by_email={"a@x.com": [(999, "Other")]},
    )
    assert out[100] == PosterResolution(site_user_id=500, legacy_username="OldName")


def test_email_fallback_when_no_forum_id():
    out = resolve_posters(
        posters={101: ("Bob", "bob@x.com")},
        forum_id_map={},
        target_user_ids={42},
        target_accounts_by_email={"bob@x.com": [(42, "Bob2")]},
    )
    assert out[101] == PosterResolution(site_user_id=42, legacy_username="Bob")


def test_forum_id_target_missing_falls_through_to_archived():
    # forum_id points at a user_id that doesn't exist in the target (e.g. dev
    # subset) and no email match → Archived User.
    out = resolve_posters(
        posters={102: ("Ghost", "")},
        forum_id_map={102: 700},
        target_user_ids=set(),
        target_accounts_by_email={},
    )
    assert out[102] == PosterResolution(site_user_id=None, legacy_username="Ghost")


def test_username_only_is_not_trusted():
    # A username-only match is deliberately NOT resolved to a real account.
    out = resolve_posters(
        posters={103: ("SharedName", "")},
        forum_id_map={},
        target_user_ids={1},
        target_accounts_by_email={},
    )
    assert out[103].site_user_id is None
    assert out[103].legacy_username == "SharedName"


def test_shared_email_username_picks_the_matching_account():
    # Two site accounts share the address (ADR-0012); the phpBB username is the
    # second factor. The matching account is listed first so "last row wins"
    # would pick the wrong one.
    out = resolve_posters(
        posters={104: ("Juugo", "j@x.com")},
        forum_id_map={},
        target_user_ids={10, 11},
        target_accounts_by_email={"j@x.com": [(10, "Juugo"), (11, "Kyuu")]},
    )
    assert out[104] == PosterResolution(site_user_id=10, legacy_username="Juugo")


def test_shared_email_without_username_match_is_archived():
    out = resolve_posters(
        posters={105: ("Eimi", "e@x.com")},
        forum_id_map={},
        target_user_ids={20, 21},
        target_accounts_by_email={"e@x.com": [(20, "EimiYams"), (21, "Synthesis")]},
    )
    assert out[105] == PosterResolution(site_user_id=None, legacy_username="Eimi")


def test_shared_email_username_match_is_case_insensitive():
    out = resolve_posters(
        posters={106: ("primetch", "p@x.com")},
        forum_id_map={},
        target_user_ids={30, 31},
        target_accounts_by_email={"p@x.com": [(30, "PrimeTch"), (31, "primetech")]},
    )
    assert out[106] == PosterResolution(site_user_id=30, legacy_username="primetch")


def test_shared_email_with_several_username_matches_is_archived():
    out = resolve_posters(
        posters={107: ("Dup", "d@x.com")},
        forum_id_map={},
        target_user_ids={40, 41},
        target_accounts_by_email={"d@x.com": [(40, "Dup"), (41, "dup")]},
    )
    assert out[107].site_user_id is None


def test_forum_id_beats_shared_email_match():
    out = resolve_posters(
        posters={108: ("Juugo", "j@x.com")},
        forum_id_map={108: 500},
        target_user_ids={10, 11, 500},
        target_accounts_by_email={"j@x.com": [(10, "Juugo"), (11, "Kyuu")]},
    )
    assert out[108].site_user_id == 500
