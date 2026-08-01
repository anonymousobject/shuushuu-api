"""Integration tests for the artist-identity backfill."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity_backfill import run_backfill


@pytest.mark.integration
class TestBackfillExistingLinks:
    async def test_parses_identity_onto_existing_pixiv_link(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(title="TKennshou", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(tag_id=artist.tag_id, url="https://www.pixiv.net/users/21412050")
        )
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert (link.site, link.external_id) == ("pixiv", "21412050")
        assert report.links_parsed == 1
        assert report.artist_tags_without_identity == 0

    async def test_dry_run_changes_nothing(self, db_session: AsyncSession) -> None:
        artist = Tags(title="TKennshou", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(tag_id=artist.tag_id, url="https://www.pixiv.net/users/21412050")
        )
        await db_session.commit()

        report = await run_backfill(db_session, apply=False)
        assert report.links_parsed == 1

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert link.site is None


@pytest.mark.integration
class TestBackfillFromAliases:
    async def test_alias_title_creates_link_on_canonical(self, db_session: AsyncSession) -> None:
        artist = Tags(title="SomeArtist", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(Tags(title="Pixiv 21412050", type=TagType.ARTIST, alias_of=artist.tag_id))
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert link.url == "https://www.pixiv.net/users/21412050"
        assert (link.site, link.external_id) == ("pixiv", "21412050")
        assert report.links_created_from_aliases == 1

    async def test_conflicting_alias_is_an_anomaly_not_a_write(
        self, db_session: AsyncSession
    ) -> None:
        # artist A owns pixiv 111 via a link; artist B has alias "Pixiv 111"
        artist_a = Tags(title="ArtistA", type=TagType.ARTIST, usage_count=1)
        artist_b = Tags(title="ArtistB", type=TagType.ARTIST, usage_count=1)
        db_session.add_all([artist_a, artist_b])
        await db_session.flush()
        db_session.add(
            TagExternalLinks(tag_id=artist_a.tag_id, url="https://www.pixiv.net/users/111")
        )
        db_session.add(Tags(title="Pixiv 111", type=TagType.ARTIST, alias_of=artist_b.tag_id))
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)
        assert any("111" in a for a in report.anomalies)
        # artist B gained no link
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist_b.tag_id)
            )
        ).first() is None


@pytest.mark.integration
class TestBackfillFromDesc:
    async def test_desc_url_creates_link(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="OldArtist",
            type=TagType.ARTIST,
            desc="profile: https://www.pixiv.net/member.php?id=555",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert link.url == "https://www.pixiv.net/users/555"
        assert (link.site, link.external_id) == ("pixiv", "555")
        assert report.links_created_from_desc == 1
        assert artist.desc == "profile: https://www.pixiv.net/member.php?id=555"

    async def test_multiple_distinct_desc_ids_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="ConfusedArtist",
            type=TagType.ARTIST,
            desc="https://www.pixiv.net/member.php?id=1 https://www.pixiv.net/users/2",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert len(report.anomalies) == 1
        assert "1" in report.anomalies[0] and "2" in report.anomalies[0]
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).first() is None


@pytest.mark.integration
class TestArtistTagsWithoutIdentity:
    async def test_counts_canonical_artists_missing_from_every_source(
        self, db_session: AsyncSession
    ) -> None:
        artist_with_link = Tags(title="HasLink", type=TagType.ARTIST, usage_count=1)
        artist_without_identity = Tags(title="NoIdentity", type=TagType.ARTIST, usage_count=1)
        db_session.add_all([artist_with_link, artist_without_identity])
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist_with_link.tag_id, url="https://www.pixiv.net/users/999"
            )
        )
        # An alias of artist_with_link, itself typed ARTIST but with alias_of set. Its
        # title doesn't match the "Pixiv <id>" pattern, so it contributes no identity —
        # it must still be excluded from the canonical-artist population entirely
        # (not counted as "without identity" in its own right).
        db_session.add(
            Tags(title="SomeAliasTitle", type=TagType.ARTIST, alias_of=artist_with_link.tag_id)
        )
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        # Only artist_without_identity lacks identity; the alias tag isn't a canonical
        # artist tag (alias_of is set) so it isn't part of the counted population at all.
        assert report.artist_tags_without_identity == 1
