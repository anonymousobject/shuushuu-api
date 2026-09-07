"""Integration tests pinning DB-level case-insensitive identity semantics.

ADR-0008: `tag_external_links.site` / `.external_id` are ci_string --
VARCHAR(n) on MariaDB (case-insensitive via utf8mb4_unicode_ci) and CITEXT on
Postgres. `resolve_identity` does a plain `==` comparison in SQL; the
case-folding guarantee has to live in the column type, not the query, so
these tests go straight at the DB rather than mocking anything.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity import ArtistIdentity, resolve_identity


@pytest.mark.integration
class TestResolveIdentityCaseInsensitive:
    async def test_matches_case_variant_external_id(self, db_session: AsyncSession) -> None:
        """A link stored with a mixed-case external_id resolves when queried
        in a different case. Real pixiv ids are digit-only (case can't vary
        for them today), so the stored value is deliberately not a real
        pixiv id -- this pins the column-type guarantee for whatever site
        registers next with alphanumeric ids."""
        artist = Tags(title="CaseFoldArtist", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://example.com/artist/AbC123",
                site="pixiv",
                external_id="AbC123",
            )
        )
        await db_session.commit()

        found = await resolve_identity(
            db_session, ArtistIdentity(site="pixiv", external_id="abc123")
        )

        assert found is not None
        assert found.tag_id == artist.tag_id

    async def test_matches_case_variant_site(self, db_session: AsyncSession) -> None:
        """Same guarantee for `site` -- both columns are ci_string."""
        artist = Tags(title="CaseFoldArtistSite", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://example.com/artist/99999999",
                site="pixiv",
                external_id="99999999",
            )
        )
        await db_session.commit()

        found = await resolve_identity(
            db_session, ArtistIdentity(site="PIXIV", external_id="99999999")
        )

        assert found is not None
        assert found.tag_id == artist.tag_id

    async def test_case_variant_does_not_falsely_match_a_different_id(
        self, db_session: AsyncSession
    ) -> None:
        """Negative control: case-folding must not turn into substring or
        fuzzy matching -- a genuinely different external_id still misses."""
        artist = Tags(title="CaseFoldArtistNegative", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://example.com/artist/AbC123",
                site="pixiv",
                external_id="AbC123",
            )
        )
        await db_session.commit()

        found = await resolve_identity(
            db_session, ArtistIdentity(site="pixiv", external_id="abc124")
        )

        assert found is None
