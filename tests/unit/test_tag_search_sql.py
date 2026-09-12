"""build_search is pure: assert the SQL shape and bind parameters, no database."""

from datetime import date, datetime

import pytest

from app.services.tag_search import SearchFilters, build_search


def _build(query: str, filters: SearchFilters | None = None, **overrides):
    kwargs = {"limit": 10, "offset": 0, "filters": filters or SearchFilters(), "sort": None}
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

    def test_relevance_order_is_tier_typos_position_usage_id(self):
        st = _build("sakura kino")
        assert (
            "ORDER BY tier, typos, pos, eff_usage DESC, tag_id ASC LIMIT :limit OFFSET :offset"
            in st.ids_sql
        )
        assert (
            "CASE WHEN tiers.tier < 4 THEN 0 ELSE (" in st.ids_sql
        )  # typo keys are lazy, tier 4 only

    def test_words_are_capped_for_levenshtein(self):
        st = _build("sakura kino")
        assert (
            st.ids_sql.count("ARRAY(SELECT left(w, 255) FROM unnest(") == 2
        )  # query words and title words

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
        st = _build("sakura", filters=SearchFilters(type_filter=4, aliases="hide"))
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

    @pytest.mark.parametrize("query", ["", "sa", "sakura"])
    def test_aliases_tri_state_reaches_ids_and_count(self, query: str):
        hide = _build(query, SearchFilters(aliases="hide"))
        only = _build(query, SearchFilters(aliases="only"))
        every = _build(query, SearchFilters(aliases="all"))
        for sql in (hide.ids_sql, hide.count_sql):
            assert "t.alias_of IS NULL" in sql
        for sql in (only.ids_sql, only.count_sql):
            assert "t.alias_of IS NOT NULL" in sql
        for sql in (every.ids_sql, every.count_sql):
            assert "alias_of IS" not in sql

    @pytest.mark.parametrize("query", ["", "sa", "sakura"])
    def test_min_usage_uses_effective_count_and_joins_parent_in_count(self, query: str):
        st = _build(query, SearchFilters(min_usage=50))
        for sql in (st.ids_sql, st.count_sql):
            assert "COALESCE(parent.usage_count, t.usage_count) >= :min_usage" in sql
        assert "LEFT JOIN tags parent ON parent.tag_id = t.alias_of" in st.count_sql
        assert st.params["min_usage"] == 50

    def test_count_skips_parent_join_without_min_usage(self):
        st = _build("sakura", SearchFilters(aliases="hide"))
        assert "parent" not in st.count_sql

    def test_added_range_binds_naive_utc_midnights(self):
        st = _build("", SearchFilters(added_from=date(2020, 6, 1), added_to=date(2020, 6, 30)))
        assert "t.date_added >= :added_from_ts" in st.ids_sql
        assert "t.date_added < :added_to_next_ts" in st.count_sql
        assert st.params["added_from_ts"] == datetime(2020, 6, 1)
        assert st.params["added_to_next_ts"] == datetime(2020, 7, 1)
        assert st.params["added_from_ts"].tzinfo is None

    def test_added_from_alone(self):
        st = _build("", SearchFilters(added_from=date(2021, 1, 1)))
        assert "added_from_ts" in st.ids_sql
        assert "added_to_next_ts" not in st.ids_sql

    def test_has_alias_yes_and_no(self):
        yes = _build("", SearchFilters(has_alias="yes"))
        no = _build("", SearchFilters(has_alias="no"))
        assert "EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in yes.ids_sql
        assert "NOT EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in no.count_sql
        assert "NOT EXISTS" not in yes.ids_sql

    def test_is_child_yes_and_no(self):
        assert (
            "t.inheritedfrom_id IS NOT NULL" in _build("", SearchFilters(is_child="yes")).count_sql
        )
        assert "t.inheritedfrom_id IS NULL" in _build("", SearchFilters(is_child="no")).ids_sql

    def test_has_children_yes_and_no(self):
        yes = _build("", SearchFilters(has_children="yes"))
        no = _build("", SearchFilters(has_children="no"))
        assert (
            "EXISTS (SELECT 1 FROM tags child WHERE child.inheritedfrom_id = t.tag_id)"
            in yes.ids_sql
        )
        assert (
            "NOT EXISTS (SELECT 1 FROM tags child WHERE child.inheritedfrom_id = t.tag_id)"
            in no.ids_sql
        )

    def test_source_linked_picks_the_column_by_type(self):
        character = _build("", SearchFilters(type_filter=4, source_linked="yes"))
        source = _build("", SearchFilters(type_filter=2, source_linked="no"))
        assert (
            "EXISTS (SELECT 1 FROM character_source_links l WHERE l.character_tag_id = t.tag_id)"
            in character.ids_sql
        )
        assert (
            "NOT EXISTS (SELECT 1 FROM character_source_links l WHERE l.source_tag_id = t.tag_id)"
            in source.count_sql
        )

    @pytest.mark.parametrize("type_filter", [None, 1, 3])
    def test_source_linked_without_character_or_source_type_is_rejected(self, type_filter):
        with pytest.raises(ValueError, match="source_linked"):
            _build("", SearchFilters(type_filter=type_filter, source_linked="yes"))

    def test_is_structural_flag(self):
        assert not SearchFilters().is_structural
        assert not SearchFilters(type_filter=4, aliases="hide").is_structural
        assert SearchFilters(min_usage=0).is_structural
        assert SearchFilters(added_to=date(2020, 1, 1)).is_structural
        assert SearchFilters(has_children="no").is_structural

    def test_filters_combine_with_query_and_type(self):
        st = _build(
            "sakura", SearchFilters(type_filter=4, aliases="hide", min_usage=10, has_alias="no")
        )
        for sql in (st.ids_sql, st.count_sql):
            assert "t.type = :type_filter" in sql
            assert "t.alias_of IS NULL" in sql
            assert ">= :min_usage" in sql
            assert "NOT EXISTS (SELECT 1 FROM tags a WHERE a.alias_of = t.tag_id)" in sql
