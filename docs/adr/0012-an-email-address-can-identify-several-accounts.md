# An email address can identify several accounts

`users.email` is not unique and cannot be made unique. Any code that resolves a
user by email must expect several rows, and any flow that acts on "the account
with this email" needs a second factor to decide which one. Settled in PR #369.

The problem this settled: `/auth/forgot-password` and `/auth/reset-password`
both resolved the account with `scalar_one_or_none()`, which raises
`MultipleResultsFound` on a second row. Nothing caught it, so a member whose
address is shared got a 500 and the frontend's generic "Request failed" — with
no way to recover the account. `_update_user_profile` had the same crash on its
email-uniqueness check. This was live in prod for every one of the ~1000
affected members until PR #369.

## Why uniqueness is not available

Signups under the legacy PHP site never enforced it. Registration blocks new
duplicates today (`app/api/v1/users.py`, the username-or-email 409), so the set
is closed and historical — but it is large, and it is not disposable:

| | |
|---|---|
| accounts on a shared email | 1027 |
| email addresses affected | 480 |
| groups with 2+ **active** accounts | 448 |
| groups where 2+ accounts hold images or comments | 236 |
| accounts with zero images and zero comments | 402 |
| largest single group | 9 accounts |

Retiring the 402 empty shells would clear 244 of the 480 groups. The remaining
236 have two or more accounts holding real uploads and comments —
`flowergirl1233@hotmail.com` pairs a 1528-image account with three smaller ones
that still hold comments. Collapsing those means reassigning or destroying
attribution on content going back to 2008, on accounts that in most cases
nobody has touched in a decade. A unique index is therefore not reachable by
cleanup, and adding one would require deleting member history to satisfy a
constraint no feature needs.

## The token is the account selector

For password reset the second factor is the reset token, not the email:

- `forgot-password` issues a **separate token per active account** on the
  address and queues one email each. The email template greets its account by
  username, which is what lets the recipient tell the links apart.
- `reset-password` still takes the email, but the **token** picks which of the
  matching accounts is reset. The others' passwords and pending tokens are left
  alone.

Each account's row carries its own `password_reset_token` /
`password_reset_sent_at` / `password_reset_expires_at`, so the per-account
5-minute rate limit also evaluates independently — one account's recent reset
must not suppress another's link, or the second account becomes unrecoverable.

Disabled accounts are skipped, as they always were. This is load-bearing rather
than incidental: the case that surfaced the bug was a member who had
deliberately disabled her older, larger account and needed the reset to reach
the newer one.

## Considered Options

- **Pick one account** — most recent login, or prefer active. The smallest
  diff that stops the 500. Rejected: 448 groups have two or more active
  accounts, so for most of them this silently hands the member a link for an
  account they did not ask about, with no way to reach the other one.
- **Restrict to a unique account by requiring username + email** — correct and
  unambiguous, but an API contract change plus frontend work, and it degrades
  the flow for the ~99% of members with no duplicate, who would have to
  remember a username to recover the account whose username they forgot.
- **Deduplicate the accounts, then add a unique index** — see above; it does
  not reach 236 of the groups without destroying history, and the crash would
  remain live for as long as the cleanup took.
- **A token per account, token selects on reset (chosen)** — no schema change,
  no contract change, no data decisions, and it is correct for every group size
  including the 9-account one. The cost is fan-out: one request to the largest
  address sends 8 emails. The per-account rate limit bounds this to one email
  per account per 5 minutes.

## Consequences

Anything resolving a user by email — support tooling, future flows, admin
lookups — must handle multiple rows. `scalar_one_or_none()` on an
`email ==` filter is a latent 500 anywhere it appears. Prefer `scalars().all()`
and an explicit decision about which account, or `.limit(1)` with
`scalars().first()` where a single match is only being tested for existence.
