"""Tag search in Postgres with pg_trgm.

Design: docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md.

`gates_for` and `build_search` are pure: they turn a query into SQL text and
bind parameters and are unit-tested without a database. `search_tags` runs
the statements. The SQL depends on the objects created by migration
0004_tag_search_fold: `public.fold_search_text(text)` and the four
`ix_*_fold_*` indexes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

# Three consecutive letters or digits (underscore excluded): the shortest
# literal a trigram index can look up.
_ALNUM_RUN = re.compile(r"[^\W_]{3}")
# A token of only digits is an identifier (pixiv artist ID, year): it must
# match literally, never fuzzily.
_DIGITS = re.compile(r"^\d+$")

# Meilisearch's minimum word length for one typo; below it a word matches
# literally only.
MIN_FUZZY_LETTERS = 5
# A one-character token contains-matches nearly every description.
MIN_SECONDARY_TOKEN_CHARS = 2


@dataclass(frozen=True)
class TagSearchResult:
    """Ordered tag ids for one page, plus the exact total."""

    tag_ids: list[int]
    total: int


@dataclass(frozen=True)
class SearchGates:
    """Which candidate branches a query runs."""

    prefix_only: bool  # title-prefix path only: no CTE, no fuzzy/desc/URL
    fuzzy: bool  # trigram word-similarity branch
    secondary: bool  # description and external-URL branches


def _letter_count(token: str) -> int:
    return sum(char.isalpha() for char in token)


def gates_for(query: str, tokens: list[str]) -> SearchGates:
    """Decide the branches for `query` (already stripped) and its whitespace tokens."""
    prefix_only = len(query) < 3 or _ALNUM_RUN.search(query) is None
    fuzzy = not prefix_only and any(_letter_count(token) >= MIN_FUZZY_LETTERS for token in tokens)
    secondary = not prefix_only and all(len(token) >= MIN_SECONDARY_TOKEN_CHARS for token in tokens)
    return SearchGates(prefix_only=prefix_only, fuzzy=fuzzy, secondary=secondary)
