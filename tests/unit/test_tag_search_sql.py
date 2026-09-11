"""build_search is pure: assert the SQL shape and bind parameters, no database."""

import pytest

from app.services.tag_search import build_search


def _build(query: str, **overrides):
    kwargs = {"limit": 10, "offset": 0, "type_filter": None, "exclude_aliases": False, "sort": None}
    kwargs.update(overrides)
    return build_search(query, **kwargs)


@pytest.mark.unit
class TestBuildSearch:
    def test_empty_query_lists_all_by_effective_usage(self):
        st = _build("")
        assert "candidates" not in st.ids_sql
        assert "COALESCE(parent.usage_count, t.usage_count) DESC, t.tag_id ASC" in st.ids_sql
        assert st.count_sql.strip().startswith("SELECT count(*) FROM tags t")
        assert st.params == {"limit": 10, "offset": 0}

    def test_prefix_only_query_has_no_cte(self):
        st = _build("sa")
        assert "WITH" not in st.ids_sql
        assert (
            "fold_search_text(t.title::text) LIKE public.fold_search_text(:prefix_q)" in st.ids_sql
        )
        assert st.params["prefix_q"] == "sa%"
        assert "levenshtein" not in st.ids_sql

    def test_general_query_unions_all_four_branches(self):
        st = _build("sakura kino")
        assert st.ids_sql.count("UNION") == 3
        assert "<%" in st.ids_sql  # fuzzy: "sakura" has 6 letters
        assert 'fold_search_text("desc")' in st.ids_sql
        assert "FROM tag_external_links WHERE" in st.ids_sql
        assert st.params["like_tok0"] == "%sakura%"
        assert st.params["like_tok1"] == "%kino%"
        assert st.params["like_q"] == "%sakura kino%"
        assert st.params["q"] == "sakura kino"

    def test_short_letters_skip_fuzzy_and_one_char_token_skips_secondary(self):
        st = _build("the f")
        assert "<%" not in st.ids_sql
        assert "tag_external_links" not in st.ids_sql
        assert "UNION" not in st.ids_sql

    def test_numeric_token_adds_literal_clause_and_no_fuzzy(self):
        st = _build("21412050")
        assert "<%" not in st.ids_sql
        assert st.ids_sql.count("EXISTS (SELECT 1 FROM tag_external_links l") == 1
        assert st.count_sql.count("EXISTS (SELECT 1 FROM tag_external_links l") == 1

    def test_like_metacharacters_are_escaped(self):
        st = _build("100%")
        assert st.params["like_tok0"] == "%100\\%%"
        assert st.params["prefix_q"] == "100\\%%"

    def test_filters_apply_to_ids_and_count(self):
        st = _build("sakura", type_filter=4, exclude_aliases=True)
        for sql in (st.ids_sql, st.count_sql):
            assert "t.type = :type_filter" in sql
            assert "t.alias_of IS NULL" in sql
        assert st.params["type_filter"] == 4

    def test_explicit_sort_replaces_relevance(self):
        st = _build("sakura", sort=["title:asc"])
        assert "ORDER BY t.title ASC, t.tag_id ASC" in st.ids_sql
        assert "levenshtein" not in st.ids_sql

    def test_usage_count_sort_uses_effective_count(self):
        st = _build("", sort=["usage_count:desc"])
        assert (
            "ORDER BY COALESCE(parent.usage_count, t.usage_count) DESC, t.tag_id DESC" in st.ids_sql
        )

    def test_unknown_sort_field_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported sort field"):
            _build("sakura", sort=["not_a_field:asc"])

    def test_user_text_never_appears_in_sql(self):
        st = _build("'; DROP TABLE tags; --")
        assert "DROP TABLE" not in st.ids_sql
        assert "DROP TABLE" not in st.count_sql
