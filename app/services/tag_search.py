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
from typing import Any

from app.core.logging import get_logger
from app.utils.like_escape import escape_like_pattern

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


# Meilisearch sort fields -> SQL. usage_count resolves to the effective count so
# alias rows rank by their parent's popularity.
_EFFECTIVE_USAGE = "COALESCE(parent.usage_count, t.usage_count)"
_SORT_COLUMNS = {
    "usage_count": _EFFECTIVE_USAGE,
    "title": "t.title",
    "type": "t.type",
    "date_added": "t.date_added",
    "tag_id": "t.tag_id",
}

# The word list of a folded string: split on runs that are not letters,
# digits, or underscore; drop empties. The same expression tokenizes the
# query (`:q`) and each title, so both sides agree on what a word is.
_WORDS = (
    "array_remove(regexp_split_to_array(public.fold_search_text({expr}), '[^[:alnum:]_]+'), '')"
)

_BASE_FROM = "FROM tags t LEFT JOIN tags parent ON parent.tag_id = t.alias_of"
_DESC_FOLD = 'public.fold_search_text("desc")'


@dataclass(frozen=True)
class SearchStatements:
    """The two statements one search runs, sharing one parameter dict."""

    ids_sql: str
    count_sql: str
    params: dict[str, Any]


def _contains_all(expr: str, token_count: int) -> str:
    """`expr` contains every token: AND of folded LIKE per token."""
    return " AND ".join(
        f"{expr} LIKE public.fold_search_text(:like_tok{i})" for i in range(token_count)
    )


def _order_clause(sort: list[str] | None) -> str | None:
    if not sort:
        return None
    field, _, direction_token = sort[0].partition(":")
    if field not in _SORT_COLUMNS:
        raise ValueError(f"Unsupported sort field: {field!r}")
    direction = "ASC" if direction_token.lower() == "asc" else "DESC"
    return f"{_SORT_COLUMNS[field]} {direction}, t.tag_id {direction}"


def build_search(
    query: str,
    *,
    limit: int,
    offset: int,
    type_filter: int | None,
    exclude_aliases: bool,
    sort: list[str] | None,
) -> SearchStatements:
    """Build the ids and count statements for one search. Pure."""
    query = query.strip()
    tokens = query.split()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    filters: list[str] = []
    if type_filter is not None:
        filters.append("t.type = :type_filter")
        params["type_filter"] = type_filter
    if exclude_aliases:
        filters.append("t.alias_of IS NULL")
    order = _order_clause(sort)

    # Empty query: list every tag.
    if not tokens:
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        ids_sql = (
            f"SELECT t.tag_id {_BASE_FROM} {where} "
            f"ORDER BY {order or f'{_EFFECTIVE_USAGE} DESC, t.tag_id ASC'} "
            "LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, f"SELECT count(*) FROM tags t {where}", params)

    params["q"] = query
    params["prefix_q"] = f"{escape_like_pattern(query)}%"
    for i, token in enumerate(tokens):
        params[f"like_tok{i}"] = f"%{escape_like_pattern(token)}%"
    gates = gates_for(query, tokens)

    # Short or sub-trigram query: title prefix on the btree index, nothing else.
    if gates.prefix_only:
        filters.insert(
            0, "public.fold_search_text(t.title::text) LIKE public.fold_search_text(:prefix_q)"
        )
        where = f"WHERE {' AND '.join(filters)}"
        exact_first = (
            "CASE WHEN public.fold_search_text(t.title::text) = public.fold_search_text(:q) "
            "THEN 0 ELSE 1 END"
        )
        ids_sql = (
            f"SELECT t.tag_id {_BASE_FROM} {where} "
            f"ORDER BY {order or f'{exact_first}, {_EFFECTIVE_USAGE} DESC, t.tag_id ASC'} "
            "LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, f"SELECT count(*) FROM tags t {where}", params)

    # General path: the candidate set is a UNION, never an OR — an OR that
    # mixes these branches defeats the planner's bitmap and scans the table.
    branches = [
        f"SELECT tag_id FROM tags WHERE {_contains_all('public.fold_search_text(title::text)', len(tokens))}"
    ]
    if gates.fuzzy:
        branches.append(
            "SELECT tag_id FROM tags WHERE public.fold_search_text(:q) <% public.fold_search_text(title::text)"
        )
    if gates.secondary:
        params["like_q"] = f"%{escape_like_pattern(query)}%"
        branches.append(f"SELECT tag_id FROM tags WHERE {_contains_all(_DESC_FOLD, len(tokens))}")
        branches.append(
            "SELECT tag_id FROM tag_external_links "
            "WHERE public.fold_search_text(url) LIKE public.fold_search_text(:like_q)"
        )
    # Digits match literally somewhere, whatever branch admitted the row.
    for i, token in enumerate(tokens):
        if _DIGITS.match(token):
            filters.append(
                f"(public.fold_search_text(t.title::text) LIKE public.fold_search_text(:like_tok{i})"
                f' OR public.fold_search_text(t."desc") LIKE public.fold_search_text(:like_tok{i})'
                f" OR EXISTS (SELECT 1 FROM tag_external_links l WHERE l.tag_id = t.tag_id"
                f" AND public.fold_search_text(l.url) LIKE public.fold_search_text(:like_tok{i})))"
            )
    candidates = "candidates AS (\n    " + "\n    UNION\n    ".join(branches) + "\n)"
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    count_sql = f"WITH {candidates} SELECT count(*) FROM tags t JOIN candidates c ON c.tag_id = t.tag_id {where}"

    if order:
        ids_sql = (
            f"WITH {candidates} SELECT t.tag_id {_BASE_FROM} JOIN candidates c ON c.tag_id = t.tag_id "
            f"{where} ORDER BY {order} LIMIT :limit OFFSET :offset"
        )
        return SearchStatements(ids_sql, count_sql, params)

    # Relevance. Tiers 0-3 are literal matches, so their typo count is 0 by
    # construction and the CASE keeps the levenshtein work for tier 4 only.
    qwords = (
        "qwords AS (SELECT w, ord, ord = max(ord) OVER () AS is_last "
        f"FROM unnest({_WORDS.format(expr=':q')}) WITH ORDINALITY AS u(w, ord))"
    )
    ids_sql = f"""WITH {qwords},
{candidates},
scored AS (
    SELECT t.tag_id, {_EFFECTIVE_USAGE} AS eff_usage, tiers.tier,
        CASE WHEN tiers.tier < 4 THEN 0 ELSE (
            SELECT sum(best.d) FROM qwords CROSS JOIN LATERAL (
                SELECT min(CASE WHEN qwords.is_last
                               THEN least(levenshtein(qwords.w, x), levenshtein(qwords.w, left(x, length(qwords.w))))
                               ELSE levenshtein(qwords.w, x) END) AS d
                FROM unnest(f.tw) AS x) AS best) END AS typos,
        CASE WHEN tiers.tier < 4 THEN COALESCE((
            SELECT min(xo.ord) FROM unnest(f.tw) WITH ORDINALITY AS xo(x, ord), qwords
            WHERE position(qwords.w IN xo.x) > 0), 999)
        ELSE COALESCE((
            SELECT min(xo.ord) FROM unnest(f.tw) WITH ORDINALITY AS xo(x, ord), qwords
            WHERE levenshtein(qwords.w, xo.x) = (SELECT min(levenshtein(qwords.w, x2)) FROM unnest(f.tw) AS x2)), 999) END AS pos
    {_BASE_FROM}
    JOIN candidates c ON c.tag_id = t.tag_id
    CROSS JOIN LATERAL (SELECT public.fold_search_text(t.title::text) AS ft, {_WORDS.format(expr="t.title::text")} AS tw) AS f
    CROSS JOIN LATERAL (SELECT CASE
        WHEN f.ft = public.fold_search_text(:q) THEN 0
        WHEN f.ft LIKE public.fold_search_text(:prefix_q) THEN 1
        WHEN NOT EXISTS (SELECT 1 FROM qwords WHERE NOT is_last AND NOT (w = ANY(f.tw)))
         AND EXISTS (SELECT 1 FROM unnest(f.tw) AS x, qwords WHERE qwords.is_last AND left(x, length(qwords.w)) = qwords.w) THEN 2
        WHEN {_contains_all("f.ft", len(tokens))} THEN 3
        ELSE 4 END AS tier) AS tiers
    {where}
)
SELECT tag_id FROM scored ORDER BY tier, typos, pos, eff_usage DESC, tag_id ASC LIMIT :limit OFFSET :offset"""
    return SearchStatements(ids_sql, count_sql, params)
