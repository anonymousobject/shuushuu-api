"""Translate a user's comment-search string into SQL predicates.

InnoDB fulltext offers two modes and both are wrong as a default:

* NATURAL LANGUAGE MODE ORs its terms. On the dev corpus `happy birthday`
  matched 9,671 comments, of which only 2,059 contain the phrase.
* BOOLEAN MODE ANDs correctly with `+term`, but returns *zero* rows for any
  term the index cannot see. `+happy +bd` and `+the +cat` both match nothing.

So we split the query across both mechanisms: terms the index can see go into
a BOOLEAN MODE conjunction (fast, index-backed), and everything else falls
back to LIKE. Every term is ANDed either way, and no single term can zero out
the result set.

Only `\\w+` runs ever reach the boolean string. That is a hard requirement,
not a stylistic one: a stray `@` makes MySQL raise ERROR 1064, which would
surface as a 500 on the default search path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Must match innodb_ft_min_token_size on the server. Anything shorter is
# absent from the index, so `+ab` matches nothing at all.
MIN_TOKEN_SIZE = 3

# information_schema.INNODB_FT_DEFAULT_STOPWORD, verbatim. A stopword inside a
# boolean conjunction is not ignored -- it makes the whole conjunction fail.
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "are",
        "as",
        "at",
        "be",
        "by",
        "com",
        "de",
        "en",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "la",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "und",
        "was",
        "what",
        "when",
        "where",
        "who",
        "will",
        "with",
        "www",
    }
)

# A quoted phrase (optionally negated), or a bare run of non-space characters.
_TERM_RE = re.compile(r'-?"[^"]*"|\S+')

# Word characters only. The fulltext parser breaks on everything else, so
# "well-known" is two tokens to the index and must be two tokens to us.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class CommentSearchQuery:
    """The predicates a parsed search string maps onto."""

    boolean_query: str = ""
    like_terms: list[str] = field(default_factory=list)
    not_like_terms: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.boolean_query or self.like_terms or self.not_like_terms)


def like_pattern(term: str) -> str:
    """Build a contains-pattern, escaping LIKE metacharacters.

    Without this a search for `100%` degrades into "match anything". Backslash
    is escaped first so it cannot double-escape the wildcards added after it.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _is_indexable(token: str) -> bool:
    """Whether the fulltext index can actually see this token.

    Non-ASCII is excluded deliberately. With the default parser a whitespace-
    free run of Japanese is a single token, so a substring search for it misses
    rows LIKE finds -- measured at 33 vs 52 for one common term, across the
    19,200 comments that contain non-ASCII text.
    """
    return len(token) >= MIN_TOKEN_SIZE and token.isascii() and token.lower() not in STOPWORDS


def parse_comment_search(raw: str) -> CommentSearchQuery:
    """Split a user's search string into fulltext and LIKE predicates."""
    parsed = CommentSearchQuery()
    positives: list[str] = []
    negatives: list[str] = []

    for match in _TERM_RE.finditer(raw or ""):
        term = match.group(0)
        negated = term.startswith("-")
        if negated:
            term = term[1:]

        if term.startswith('"') and term.endswith('"') and len(term) >= 2:
            phrase = term[1:-1].strip()
            if not phrase:
                continue
            tokens = _WORD_RE.findall(phrase)
            if tokens and all(_is_indexable(t) for t in tokens):
                # Phrase search stays token-based, matching fulltext semantics
                # (punctuation and repeated spaces are not significant).
                quoted = '"' + " ".join(tokens) + '"'
                (negatives if negated else positives).append(quoted)
            else:
                (parsed.not_like_terms if negated else parsed.like_terms).append(phrase)
            continue

        for token in _WORD_RE.findall(term):
            if _is_indexable(token):
                (negatives if negated else positives).append(token)
            else:
                (parsed.not_like_terms if negated else parsed.like_terms).append(token)

    if positives:
        parsed.boolean_query = " ".join([f"+{p}" for p in positives] + [f"-{n}" for n in negatives])
    else:
        # A boolean query of only negative terms matches nothing in InnoDB, so
        # the exclusions have to ride on NOT LIKE when there is no positive
        # term to anchor them.
        parsed.not_like_terms.extend(n.strip('"') for n in negatives)

    return parsed
