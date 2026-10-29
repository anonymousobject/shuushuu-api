# Archived forum posts are attributed by account link or email, never by username

The phpBB archive import (`scripts/import_forum_archive.py`, api#264, design
`docs/plans/2026-Q3/2026-07-07-forum-import-design.md`) resolves each phpBB
poster to a site account in this order, in
`app/services/forum_import/user_map.py`:

1. **The legacy site's own link.** `php_shuu.users.forum_id` names the phpBB
   user an account belonged to. Authoritative, and it survives renames.
2. **Email.** The phpBB `user_email` equals a site account's email. An address
   can belong to several accounts (ADR-0012); then the phpBB username must also
   match exactly one of them, or the poster is not resolved.
3. **Otherwise the Archived User**, an inactive system account. The post keeps
   the original name in `legacy_username`, and the thread view shows it.

Against a prod restore (2026-10-29), the 2026-02-20 archive resolves 434
posters (367 by link, 67 by email) and sends 66 to the Archived User. The
shared-email rule was added in api#427 after the first version took whichever
account Postgres returned last for a shared address: it had attributed Juugo's
posts to Kyuu, primetch's to primetech, and Eimi's to Synthesis.

## Considered Options

- **Match by username** — would resolve most of the 66. Rejected: usernames
  are freed and re-registered, and accounts are renamed, so a match only says
  that somebody holds that name today. The design's analysis found 5 renamed
  accounts that a username match would have missed or mis-assigned. A post
  attributed to a stranger looks exactly like a correct one, and nothing flags
  it; "Archived User" with the original name is honest.
- **Email, any account on the address** — the first implementation. Rejected:
  arbitrary for a shared address (ADR-0012).
- **Email, single-account addresses only** — safe, but drops posters whose
  shared address and own username together pick out one account.
- **Link, then email with the username choosing among that address's accounts
  (chosen)** — every attribution rests on data that identifies the person: the
  legacy site's link, or the address they registered with. The username is
  only used to choose among accounts the email already ties to them.

## Consequences

Some real members' archived posts show as Archived User, under their old name.
Correct them by adding evidence, not by loosening the order: every imported
post keeps `legacy_poster_id` and `legacy_username`, so a known poster can be
reassigned with one `UPDATE forum_posts SET user_id = … WHERE legacy_poster_id
= …` (plus the thread author and last poster), or by adding the `forum_id` link
to the source and running the importer's `--remap`, which re-resolves everyone
and rewrites those columns without a re-import.

List views (category index, thread lists) show "Archived User", not the
original name; only the thread view swaps in `legacy_username`. Showing it in
lists would need the opening and last post's name denormalized onto the thread.

Any later tool that maps legacy identities to accounts should use the same
order, and should not accept a username match on its own.
