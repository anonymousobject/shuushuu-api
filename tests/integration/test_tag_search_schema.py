"""The tag-search migration leaves the objects the search SQL depends on."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_INDEXES = {
    "ix_tags_title_fold_trgm",
    "ix_tags_desc_fold_trgm",
    "ix_tag_external_links_url_fold_trgm",
    "ix_tags_title_fold_prefix",
}


@pytest.mark.integration
class TestTagSearchSchema:
    async def test_extensions_installed(self, db_session: AsyncSession):
        rows = await db_session.execute(
            text(
                "SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'unaccent', 'fuzzystrmatch')"
            )
        )
        assert set(rows.scalars()) == {"pg_trgm", "unaccent", "fuzzystrmatch"}

    @pytest.mark.parametrize(
        ("raw", "folded"),
        [
            ("Märchen-Noir C++", "marchen-noir c++"),
            ("EB十", "eb十"),
            ("Pokémon", "pokemon"),
            ("Louise Françoise", "louise francoise"),
        ],
    )
    async def test_fold_search_text(self, db_session: AsyncSession, raw: str, folded: str):
        value = await db_session.execute(text("SELECT public.fold_search_text(:raw)"), {"raw": raw})
        assert value.scalar_one() == folded

    async def test_search_indexes_exist(self, db_session: AsyncSession):
        rows = await db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_tag%\\_fold\\_%'")
        )
        assert set(rows.scalars()) == EXPECTED_INDEXES
