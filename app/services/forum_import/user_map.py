"""Resolve phpBB posters to current site accounts.

Trust order (identity-safe): authoritative forum_id map → email match → Archived
User. An email can identify several site accounts (ADR-0012), so a shared
address only resolves when exactly one of its accounts has the poster's
username; otherwise the poster goes to the Archived User. Username-only matches
(no email match) are intentionally not trusted (a freed username may belong to
a different person now). Pure function; the caller supplies data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PosterResolution:
    site_user_id: int | None  # None → attribute to the Archived User account
    legacy_username: str


def _account_for_email(accounts: list[tuple[int, str]], username: str) -> int | None:
    if len(accounts) == 1:
        return accounts[0][0]
    named = [uid for uid, name in accounts if name.lower() == username.lower()]
    return named[0] if len(named) == 1 else None


def resolve_posters(
    posters: dict[int, tuple[str, str]],  # phpbb_id -> (username, lower_email)
    forum_id_map: dict[int, int],  # phpbb_id -> legacy/site user_id
    target_user_ids: set[int],  # user_ids that exist in the target site
    # lower(email) -> every target account on that address as (user_id, username)
    target_accounts_by_email: dict[str, list[tuple[int, str]]],
) -> dict[int, PosterResolution]:
    out: dict[int, PosterResolution] = {}
    for phpbb_id, (username, email) in posters.items():
        site_id: int | None = None
        mapped = forum_id_map.get(phpbb_id)
        if mapped is not None and mapped in target_user_ids:
            site_id = mapped
        elif email and email in target_accounts_by_email:
            site_id = _account_for_email(target_accounts_by_email[email], username)
        out[phpbb_id] = PosterResolution(site_user_id=site_id, legacy_username=username)
    return out
