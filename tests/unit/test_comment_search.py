"""Unit tests for the comment-search query parser.

Every case here maps to a behaviour measured against the live corpus and
recorded in docs/plans/2026-08-10-comment-search-and-semantics-impl.md.
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
