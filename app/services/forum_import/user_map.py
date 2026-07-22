"""Resolve phpBB posters to current site accounts.

Trust order (identity-safe): authoritative forum_id map → email match → Archived
User. Username-only matches are intentionally not trusted (a freed username may
belong to a different person now). Pure function; the caller supplies data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PosterResolution:
    site_user_id: int | None  # None → attribute to the Archived User account
    legacy_username: str


def resolve_posters(
    posters: dict[int, tuple[str, str]],  # phpbb_id -> (username, lower_email)
    forum_id_map: dict[int, int],  # phpbb_id -> legacy/site user_id
    target_user_ids: set[int],  # user_ids that exist in the target site
    target_email_to_id: dict[str, int],  # lower(email) -> target user_id
) -> dict[int, PosterResolution]:
    out: dict[int, PosterResolution] = {}
    for phpbb_id, (username, email) in posters.items():
        site_id: int | None = None
        mapped = forum_id_map.get(phpbb_id)
        if mapped is not None and mapped in target_user_ids:
            site_id = mapped
        elif email and email in target_email_to_id:
            site_id = target_email_to_id[email]
        out[phpbb_id] = PosterResolution(site_user_id=site_id, legacy_username=username)
    return out
