"""Unit tests for the comment-search query parser.

Every case here maps to a behaviour measured against the live corpus and
recorded in
<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-08-10-comment-search-and-semantics-impl.md.
"""

from app.utils.comment_search import (
    CommentSearchQuery,
    like_pattern,
    parse_comment_search,
)


class TestParseCommentSearch:
    def test_multiple_words_are_anded(self):
        parsed = parse_comment_search("happy birthday")
        assert parsed.boolean_query == "+happy +birthday"
        assert parsed.like_terms == []
        assert parsed.not_like_terms == []

    def test_single_word(self):
        parsed = parse_comment_search("birthday")
        assert parsed.boolean_query == "+birthday"

    def test_short_token_falls_back_to_like(self):
        # `+happy +bd` returns 0 rows: `bd` is below innodb_ft_min_token_size.
        parsed = parse_comment_search("happy bd")
        assert parsed.boolean_query == "+happy"
        assert parsed.like_terms == ["bd"]

    def test_stopword_falls_back_to_like(self):
        # `+the +cat` returns 0 rows: `the` is on the InnoDB stopword list.
        parsed = parse_comment_search("the cat")
        assert parsed.boolean_query == "+cat"
        assert parsed.like_terms == ["the"]

    def test_all_terms_unindexable_produces_no_boolean_query(self):
        parsed = parse_comment_search("the of")
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["the", "of"]

    def test_non_ascii_falls_back_to_like(self):
        # The default parser cannot see inside a whitespace-free CJK run.
        parsed = parse_comment_search("かわいい")
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["かわいい"]

    def test_mixed_ascii_and_cjk(self):
        parsed = parse_comment_search("cute かわいい")
        assert parsed.boolean_query == "+cute"
        assert parsed.like_terms == ["かわいい"]

    def test_quoted_phrase_uses_boolean_phrase_syntax(self):
        parsed = parse_comment_search('"happy birthday"')
        assert parsed.boolean_query == '+"happy birthday"'
        assert parsed.like_terms == []

    def test_quoted_phrase_with_unindexable_token_falls_back_to_like(self):
        parsed = parse_comment_search('"the cat"')
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["the cat"]

    def test_phrase_and_bare_word_combine(self):
        parsed = parse_comment_search('"happy birthday" yui')
        assert parsed.boolean_query == '+"happy birthday" +yui'

    def test_negated_term_excludes(self):
        parsed = parse_comment_search("happy -sad")
        assert parsed.boolean_query == "+happy -sad"
        assert parsed.not_like_terms == []

    def test_only_negative_terms_fall_back_to_not_like(self):
        # A boolean query with no positive term matches nothing in InnoDB.
        parsed = parse_comment_search("-sad")
        assert parsed.boolean_query == ""
        assert parsed.not_like_terms == ["sad"]

    def test_negated_unindexable_term_uses_not_like(self):
        parsed = parse_comment_search("happy -ab")
        assert parsed.boolean_query == "+happy"
        assert parsed.not_like_terms == ["ab"]

    def test_boolean_operators_in_input_cannot_reach_the_query(self):
        # `+happy@birthday)` is ERROR 1064 if passed through verbatim.
        parsed = parse_comment_search("happy@birthday)")
        assert parsed.boolean_query == "+happy +birthday"
        assert "@" not in parsed.boolean_query
        assert ")" not in parsed.boolean_query

    def test_hyphenated_word_splits_into_tokens(self):
        # The fulltext parser breaks on `-`, so we must too.
        parsed = parse_comment_search("well-known")
        assert parsed.boolean_query == "+well +known"

    def test_unbalanced_quote_does_not_crash(self):
        parsed = parse_comment_search('happy "birthday')
        assert parsed.boolean_query == "+happy +birthday"

    def test_empty_input_is_empty(self):
        assert parse_comment_search("").is_empty
        assert parse_comment_search("   ").is_empty
        assert parse_comment_search("!!!").is_empty

    def test_case_is_preserved_but_stopwords_match_case_insensitively(self):
        parsed = parse_comment_search("The Cat")
        assert parsed.boolean_query == "+Cat"
        assert parsed.like_terms == ["The"]


class TestParseWithoutIndex:
    """index_visible=False (Postgres POC: no fulltext index) — everything rides LIKE.

    See docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md.
    """

    def test_indexable_token_goes_to_like(self):
        parsed = parse_comment_search("birthday", index_visible=False)
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["birthday"]

    def test_negation_still_works(self):
        parsed = parse_comment_search("happy -birthday", index_visible=False)
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["happy"]
        assert parsed.not_like_terms == ["birthday"]

    def test_quoted_phrase_becomes_single_like_term(self):
        parsed = parse_comment_search('"happy birthday"', index_visible=False)
        assert parsed.boolean_query == ""
        assert parsed.like_terms == ["happy birthday"]


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
        assert not CommentSearchQuery(boolean_query="+cat").is_empty
        assert not CommentSearchQuery(like_terms=["ab"]).is_empty
        assert not CommentSearchQuery(not_like_terms=["ab"]).is_empty


class TestIsTooShortToIndex:
    """The guard for searches worth refusing rather than table-scanning for.

    Deliberately length-based and ASCII-only. Non-ASCII must always be allowed
    through: MariaDB has no ngram parser, so CJK can only ever be served by the
    LIKE fallback, and refusing it would break Japanese comment search outright.
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
        # Tuning innodb_ft_min_token_size must not leave this guard refusing
        # terms the index can now see.
        from app.utils.comment_search import MIN_TOKEN_SIZE

        just_short = "a" * (MIN_TOKEN_SIZE - 1)
        just_long = "a" * MIN_TOKEN_SIZE
        assert parse_comment_search(just_short).is_too_short_to_index
        assert not parse_comment_search(just_long).is_too_short_to_index
