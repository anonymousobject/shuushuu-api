"""Unit tests for the comment-search query parser.

Every case here maps to a behaviour measured against the live corpus and
recorded in
<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-08-10-comment-search-and-semantics-impl.md.
"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Comments
from app.utils.comment_search import (
    CommentSearchQuery,
    apply_comment_text_search,
    like_pattern,
    parse_comment_search,
)


class TestParseCommentSearch:
    def test_multiple_words_are_anded(self):
        parsed = parse_comment_search("happy birthday")
        assert parsed.like_terms == ["happy", "birthday"]
        assert parsed.not_like_terms == []

    def test_single_word(self):
        assert parse_comment_search("birthday").like_terms == ["birthday"]

    def test_short_token_is_kept(self):
        assert parse_comment_search("happy bd").like_terms == ["happy", "bd"]

    def test_non_ascii_is_kept_whole(self):
        assert parse_comment_search("かわいい").like_terms == ["かわいい"]

    def test_mixed_ascii_and_cjk(self):
        assert parse_comment_search("cute かわいい").like_terms == ["cute", "かわいい"]

    def test_quoted_phrase_is_one_term(self):
        parsed = parse_comment_search('"happy birthday"')
        assert parsed.like_terms == ["happy birthday"]

    def test_quoted_phrase_keeps_its_punctuation(self):
        # A phrase is matched literally: "C.C." must not become "C C".
        assert parse_comment_search('"C.C."').like_terms == ["C.C."]
        assert parse_comment_search('"K-ON!" yui').like_terms == ["K-ON!", "yui"]

    def test_phrase_and_bare_word_combine(self):
        parsed = parse_comment_search('"happy birthday" yui')
        assert parsed.like_terms == ["happy birthday", "yui"]

    def test_negated_term_excludes(self):
        parsed = parse_comment_search("happy -sad")
        assert parsed.like_terms == ["happy"]
        assert parsed.not_like_terms == ["sad"]

    def test_only_negative_terms(self):
        parsed = parse_comment_search("-sad")
        assert parsed.like_terms == []
        assert parsed.not_like_terms == ["sad"]

    def test_negated_quoted_phrase(self):
        parsed = parse_comment_search('-"happy birthday"')
        assert parsed.not_like_terms == ["happy birthday"]

    def test_punctuation_splits_words(self):
        # `@` and `)` are not word characters; neither reaches a pattern.
        assert parse_comment_search("happy@birthday)").like_terms == ["happy", "birthday"]

    def test_hyphenated_word_splits_into_tokens(self):
        assert parse_comment_search("well-known").like_terms == ["well", "known"]

    def test_legacy_boolean_operators_are_dropped(self):
        # The `boolean` mode value is still accepted; its operators are not.
        parsed = parse_comment_search("+awesome -terrible word*")
        assert parsed.like_terms == ["awesome", "word"]
        assert parsed.not_like_terms == ["terrible"]

    def test_unbalanced_quote_does_not_crash(self):
        assert parse_comment_search('happy "birthday').like_terms == ["happy", "birthday"]

    def test_empty_input_is_empty(self):
        assert parse_comment_search("").is_empty
        assert parse_comment_search("   ").is_empty
        assert parse_comment_search("!!!").is_empty

    def test_case_is_preserved(self):
        # ILIKE handles case at query time; the parser leaves it alone.
        assert parse_comment_search("The Cat").like_terms == ["The", "Cat"]


class TestAppliedPredicates:
    """LIKE is case-sensitive on Postgres, so every predicate must be ILIKE
    (measured: 'birthday' matched 2790 comments case-insensitively but only
    1936 with plain LIKE)."""

    def test_default_mode_uses_ilike(self):
        query = apply_comment_text_search(select(Comments), "birthday", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "ILIKE" in sql

    def test_like_mode_uses_ilike(self):
        query = apply_comment_text_search(select(Comments), "birthday", "like")
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "ILIKE" in sql

    def test_negated_term_uses_not_ilike(self):
        query = apply_comment_text_search(select(Comments), "-birthday cake", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "NOT ILIKE" in sql

    def test_boolean_mode_behaves_as_all_words(self):
        a = apply_comment_text_search(select(Comments), "happy -sad", "boolean")
        b = apply_comment_text_search(select(Comments), "happy -sad", "all_words")
        assert str(a.compile(dialect=postgresql.dialect())) == str(
            b.compile(dialect=postgresql.dialect())
        )

    def test_nothing_searchable_matches_nothing(self):
        query = apply_comment_text_search(select(Comments), "!!!", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "WHERE false" in sql


class TestLikePattern:
    def test_wraps_in_wildcards(self):
        assert like_pattern("cat") == "%cat%"

    def test_escapes_like_metacharacters(self):
        # `100%` must search for a literal percent, not "anything".
        assert like_pattern("100%") == "%100\\%%"
        assert like_pattern("a_b") == "%a\\_b%"

    def test_escapes_backslash_first(self):
        assert like_pattern("a\\b") == "%a\\\\b%"


class TestIsEmpty:
    def test_is_empty_only_when_nothing_parsed(self):
        assert CommentSearchQuery().is_empty
        assert not CommentSearchQuery(like_terms=["ab"]).is_empty
        assert not CommentSearchQuery(not_like_terms=["ab"]).is_empty


class TestIsTooShortToIndex:
    """The guard for searches worth refusing rather than table-scanning for.

    Deliberately length-based and ASCII-only. Non-ASCII must always be allowed
    through: CJK is only ever served by the substring match, and refusing it
    would break Japanese comment search outright.
    """

    def test_single_short_ascii_term_is_too_short(self):
        assert parse_comment_search("ab").is_too_short_to_index

    def test_all_short_ascii_terms_are_too_short(self):
        assert parse_comment_search("ab cd").is_too_short_to_index

    def test_one_indexable_term_is_enough(self):
        assert not parse_comment_search("ab happy").is_too_short_to_index

    def test_non_ascii_is_never_too_short(self):
        # LIKE is the only path CJK has; refusing it would break Japanese search.
        assert not parse_comment_search("かわいい").is_too_short_to_index
        assert not parse_comment_search("猫").is_too_short_to_index

    def test_short_ascii_alongside_non_ascii_is_allowed(self):
        assert not parse_comment_search("ab かわいい").is_too_short_to_index

    def test_a_quoted_phrase_of_short_words_is_too_short(self):
        """Quoting must not smuggle a short-word search past the guard.

        A phrase is stored as one joined `like_terms` entry, so `"ab cd"` looks
        five characters long even though it is the same two two-letter words that
        `ab cd` is refused for. Both run the identical unindexed scan.
        """
        assert parse_comment_search('"ab cd"').is_too_short_to_index

    def test_a_quoted_phrase_with_a_long_word_is_allowed(self):
        assert not parse_comment_search('"ab happy"').is_too_short_to_index

    def test_a_quoted_phrase_containing_non_ascii_is_allowed(self):
        assert not parse_comment_search('"ab かわいい"').is_too_short_to_index

    def test_a_quoted_phrase_of_long_stopwords_is_allowed(self):
        # Same reasoning as the bare-word case: unindexable, but not short.
        assert not parse_comment_search('"the cat"').is_too_short_to_index

    def test_a_long_stopword_is_not_too_short(self):
        # `the` is unindexable, but it is not *short*. This guard is about length
        # only -- widening it to stopwords would also refuse "www" and "com".
        assert not parse_comment_search("the").is_too_short_to_index

    def test_nothing_searchable_is_not_reported_as_too_short(self):
        # "!!!" has no terms at all; that is the zero-rows case, not this one.
        assert not parse_comment_search("!!!").is_too_short_to_index
        assert not parse_comment_search("").is_too_short_to_index

    def test_threshold_follows_min_token_size(self):
        # Lowering MIN_TOKEN_SIZE automatically narrows this guard.
        from app.utils.comment_search import MIN_TOKEN_SIZE

        just_short = "a" * (MIN_TOKEN_SIZE - 1)
        just_long = "a" * MIN_TOKEN_SIZE
        assert parse_comment_search(just_short).is_too_short_to_index
        assert not parse_comment_search(just_long).is_too_short_to_index
