"""Translate a user's comment-search string into SQL predicates.

Every term is a case-insensitive substring match (ILIKE) and every term is
ANDed. `-term` excludes; a quoted phrase matches as one substring. The legacy
``boolean`` and ``natural`` mode values are still accepted (the frontend sends
them) and behave as the default: their operators are just punctuation here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, false

from app.models import Comments
from app.utils.like_escape import escape_like_pattern

# Below this many characters an ASCII-only search is refused (see
# CommentSearchQuery.is_too_short_to_index).
MIN_TOKEN_SIZE = 3

# Ceiling on a single comment-search statement, in seconds.
#
# A circuit breaker, not a performance policy. Comment search is an unindexed
# ILIKE scan. Measured warm on a 536k-comment corpus: ~0.5s for the count and
# ~0.8s for the page query, and — importantly — flat regardless of how many
# rows match, because the cost is the scan rather than the result set.
#
# 5s leaves roughly 6x headroom for a cold cache, concurrency and corpus
# growth. That margin is the point: if this ever fires on a real search it
# becomes a hard failure for comment search. It exists to turn a plan
# regression from minutes into an error, nothing more.
COMMENT_SEARCH_TIMEOUT_SECONDS = 5.0

# A quoted phrase (optionally negated), or a bare run of non-space characters.
_TERM_RE = re.compile(r'-?"[^"]*"|\S+')

# Word characters only: "well-known" is two terms, and stray punctuation such
# as `@` or `)` never reaches a pattern.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class CommentSearchQuery:
    """The predicates a parsed search string maps onto."""

    like_terms: list[str] = field(default_factory=list)
    not_like_terms: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.like_terms or self.not_like_terms)

    @property
    def is_too_short_to_index(self) -> bool:
        """Whether this query is short enough that refusing it costs the user nothing.

        The scan is a flat ~0.5s on the count and ~0.8s on the page query,
        independent of how many rows match, so a search of nothing but one- and
        two-character words buys a second of database time for a result nobody
        wants. Callers refuse these with a 400.

        Length-based and ASCII-only, deliberately: non-ASCII is always allowed
        (a two-character Japanese word is a real search), and long words are
        allowed whatever they are.
        """
        if self.is_empty:
            return False
        # Split back into words rather than measuring the stored entry: a quoted
        # phrase is kept as one joined string, so `"ab cd"` would otherwise
        # measure five characters and slip past the guard that refuses the very
        # same two words unquoted.
        words = [
            word
            for term in self.like_terms + self.not_like_terms
            for word in _WORD_RE.findall(term)
        ]
        return bool(words) and all(w.isascii() and len(w) < MIN_TOKEN_SIZE for w in words)


def like_pattern(term: str) -> str:
    """Build a contains-pattern, escaping LIKE metacharacters.

    Without this a search for `100%` degrades into "match anything". Backslash
    is escaped first so it cannot double-escape the wildcards added after it.
    """
    return f"%{escape_like_pattern(term)}%"


def parse_comment_search(raw: str) -> CommentSearchQuery:
    """Split a user's search string into positive and negated substring terms.

    A quoted phrase stays one term (the text between the quotes is matched
    literally); a bare term contributes one entry per word.
    """
    parsed = CommentSearchQuery()

    for match in _TERM_RE.finditer(raw or ""):
        term = match.group(0)
        negated = term.startswith("-")
        if negated:
            term = term[1:]

        target = parsed.not_like_terms if negated else parsed.like_terms
        if term.startswith('"') and term.endswith('"') and len(term) >= 2:
            phrase = term[1:-1].strip()
            if phrase:
                target.append(phrase)
            continue

        target.extend(_WORD_RE.findall(term))

    return parsed


def reject_unindexable_comment_search(raw: str, mode: str | None) -> None:
    """Raise 400 for a search of nothing but very short ASCII words.

    Only applies to the default `all_words` mode; the explicit legacy modes
    are left alone. See `CommentSearchQuery.is_too_short_to_index` for why the
    rule is length-based and never touches non-ASCII.
    """
    if (mode or "all_words") != "all_words":
        return
    if parse_comment_search(raw).is_too_short_to_index:
        raise HTTPException(
            status_code=400,
            detail=f"Comment search terms must be at least {MIN_TOKEN_SIZE} characters.",
        )


def apply_comment_text_search(query: Select[Any], raw: str, mode: str | None) -> Select[Any]:
    """Add comment-text predicates to `query`.

    `query` must already select from / join the Comments table. ``like`` mode
    matches the whole string as one substring; every other mode value goes
    through the parser (terms ANDed, `-term` excluded, quoted phrases kept
    together).

    Callers are expected to skip calling this at all for a blank/whitespace-only
    `raw` -- that means "not searching," not "search for nothing" (see the
    `commentsearch`/`search_text` guards in images.py/comments.py). A non-blank
    `raw` that still has nothing searchable (e.g. "!!!") is a query the user typed
    that can never match a comment, so it returns zero rows rather than silently
    falling back to "no filter."
    """

    def contains(pattern: str) -> Any:
        # Postgres LIKE is case-sensitive; comment search is not.
        return Comments.post_text.ilike(pattern, escape="\\")  # type: ignore[attr-defined]

    if (mode or "all_words") == "like":
        return query.where(contains(like_pattern(raw)))

    parsed = parse_comment_search(raw)
    if parsed.is_empty:
        return query.where(false())

    for term in parsed.like_terms:
        query = query.where(contains(like_pattern(term)))
    for term in parsed.not_like_terms:
        # post_text is NOT NULL (verified), so NOT LIKE needs no NULL guard.
        query = query.where(~contains(like_pattern(term)))
    return query
