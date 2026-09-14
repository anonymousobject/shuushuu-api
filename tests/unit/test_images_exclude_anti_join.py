"""_linked_to/_not_linked_to must compile to an EXISTS semi-join, never NOT IN (subquery).

Postgres can only hash a NOT IN (subquery) when it fits in work_mem (4MB); for a
popular tag (717k tag_links rows) it falls back to a per-row Materialize scan that
never finishes (#394). NOT EXISTS always plans as a hash anti-join regardless of
subquery size. This pins the SQL shape so a future edit can't regress to NOT IN.

_not_linked_to is `~_linked_to(...)`, so the positive EXISTS form is covered here
too: it's the building block the comment-filter EXISTS in list_images shares.
"""

import pytest
from sqlalchemy.dialects import postgresql

from app.api.v1.images import _linked_to, _not_linked_to
from app.models import Comments, Favorites, TagLinks


@pytest.mark.unit
class TestLinkedTo:
    def test_compiles_to_exists_with_no_not(self):
        clause = _linked_to(TagLinks, TagLinks.tag_id.in_([1, 2, 3]))
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "EXISTS" in sql
        assert "NOT" not in sql

    def test_correlates_on_image_id(self):
        clause = _linked_to(Favorites, Favorites.user_id.in_([1]))
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "favorites.image_id = images.image_id" in sql

    def test_extra_predicates_are_anded_inside_the_exists(self):
        clause = _linked_to(
            Comments,
            Comments.user_id.in_([1]),
            Comments.deleted == False,  # noqa: E712
        )
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "NOT" not in sql
        assert "posts.user_id IN" in sql
        assert "posts.deleted" in sql


@pytest.mark.unit
class TestNotLinkedTo:
    def test_compiles_to_not_exists(self):
        clause = _not_linked_to(TagLinks, TagLinks.tag_id.in_([1, 2, 3]))
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "NOT (EXISTS" in sql
        assert "NOT IN" not in sql

    def test_correlates_on_image_id(self):
        clause = _not_linked_to(Favorites, Favorites.user_id.in_([1]))
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "favorites.image_id = images.image_id" in sql

    def test_extra_predicates_are_anded_inside_the_exists(self):
        clause = _not_linked_to(
            Comments,
            Comments.user_id.in_([1]),
            Comments.deleted == False,  # noqa: E712
        )
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "NOT (EXISTS" in sql
        assert "posts.user_id IN" in sql
        assert "posts.deleted" in sql
