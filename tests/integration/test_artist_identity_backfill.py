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

    async def test_same_tag_duplicate_link_is_an_anomaly_not_a_second_row(
        self, db_session: AsyncSession
    ) -> None:
        """One tag with both the legacy member.php link and the modern
        /users/ link for the *same* pixiv id must not end up owning the
        identity twice — that'd leave two rows with an identical
        (site, external_id), which the future UNIQUE(site, external_id)
        migration can't tolerate, and it's invisible in the report unless
        flagged as an anomaly for mods to hand-review."""
        artist = Tags(title="TKennshou", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        legacy_link = TagExternalLinks(
            tag_id=artist.tag_id, url="https://www.pixiv.net/member.php?id=21412050"
        )
        modern_link = TagExternalLinks(
            tag_id=artist.tag_id, url="https://www.pixiv.net/users/21412050"
        )
        db_session.add_all([legacy_link, modern_link])
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        links = (
            (
                await db_session.execute(
                    select(TagExternalLinks)
                    .where(TagExternalLinks.tag_id == artist.tag_id)
                    .order_by(TagExternalLinks.link_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 2
        parsed = [link for link in links if link.site is not None]
        unparsed = [link for link in links if link.site is None]
        assert len(parsed) == 1
        assert (parsed[0].site, parsed[0].external_id) == ("pixiv", "21412050")
        assert len(unparsed) == 1
        assert report.links_parsed == 1
        assert any(
            "duplicate identity" in a and "21412050" in a and str(artist.tag_id) in a
            for a in report.anomalies
        )

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

    async def test_unparseable_pixiv_alias_title_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        """An alias title that's clearly *trying* to be 'Pixiv <id>' but
        doesn't match the strict pattern is worth flagging for hand-review —
        silently skipping it would hide a real identity mods could recover
        by fixing the title."""
        artist = Tags(title="SomeArtist", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(Tags(title="Pixiv-21412050", type=TagType.ARTIST, alias_of=artist.tag_id))
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert any("21412050" in a and "Pixiv-21412050" in a for a in report.anomalies)
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).first() is None
        assert report.links_created_from_aliases == 0

    async def test_plain_non_pixiv_alias_titles_stay_silent(
        self, db_session: AsyncSession
    ) -> None:
        """A regular alias title unrelated to pixiv shouldn't trip the loose
        probe and shouldn't appear in the anomaly report at all."""
        artist = Tags(title="SomeArtist", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.flush()
        db_session.add(Tags(title="Some Nickname", type=TagType.ARTIST, alias_of=artist.tag_id))
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)
        assert report.anomalies == []

    async def test_alias_pointing_at_non_artist_canonical_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        """A strict-matched 'Pixiv <id>' alias whose canonical isn't an
        artist tag is a data-modeling problem worth a human's attention —
        writing the identity link onto a non-artist canonical would be
        wrong, so the link must not be created."""
        non_artist_canonical = Tags(title="SomeTheme", type=TagType.THEME, usage_count=1)
        db_session.add(non_artist_canonical)
        await db_session.flush()
        db_session.add(
            Tags(
                title="Pixiv 55555",
                type=TagType.THEME,
                alias_of=non_artist_canonical.tag_id,
            )
        )
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert any(
            "55555" in a and str(non_artist_canonical.tag_id) in a for a in report.anomalies
        )
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(
                    TagExternalLinks.tag_id == non_artist_canonical.tag_id
                )
            )
        ).first() is None
        assert report.links_created_from_aliases == 0


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
class TestBackfillFromTitleSuffix:
    async def test_title_suffix_creates_link_on_canonical(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(title="Kuroneko (Pixiv 1000121)", type=TagType.ARTIST, usage_count=1)
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert link.url == "https://www.pixiv.net/users/1000121"
        assert (link.site, link.external_id) == ("pixiv", "1000121")
        assert report.links_created_from_titles == 1

    async def test_title_id_owned_elsewhere_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        artist_a = Tags(title="ArtistA", type=TagType.ARTIST, usage_count=1)
        artist_b = Tags(title="ArtistB (Pixiv 222)", type=TagType.ARTIST, usage_count=1)
        db_session.add_all([artist_a, artist_b])
        await db_session.flush()
        db_session.add(
            TagExternalLinks(tag_id=artist_a.tag_id, url="https://www.pixiv.net/users/222")
        )
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert any("222" in a for a in report.anomalies)
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist_b.tag_id)
            )
        ).first() is None
        assert report.links_created_from_titles == 0


@pytest.mark.integration
class TestBackfillFromBareDescText:
    async def test_bare_desc_text_creates_link(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="SomeArtist",
            type=TagType.ARTIST,
            desc="find me at pixiv 97567 thanks",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).scalar_one()
        assert link.url == "https://www.pixiv.net/users/97567"
        assert (link.site, link.external_id) == ("pixiv", "97567")
        assert report.links_created_from_desc_text == 1

    async def test_bare_text_does_not_double_fire_on_url_descs(
        self, db_session: AsyncSession
    ) -> None:
        """A desc containing only a pixiv URL is fully handled by the
        existing URL-regex source (source 3); the bare-text source must not
        also match the digits embedded in that URL."""
        artist = Tags(
            title="OldArtist",
            type=TagType.ARTIST,
            desc="profile: https://www.pixiv.net/member.php?id=555",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        links = (
            (
                await db_session.execute(
                    select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 1
        assert (links[0].site, links[0].external_id) == ("pixiv", "555")
        assert report.links_created_from_desc == 1
        assert report.links_created_from_desc_text == 0

    async def test_multiple_distinct_bare_ids_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="ConfusedArtist2",
            type=TagType.ARTIST,
            desc="pixiv 111111 or maybe pixiv 222222",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert len(report.anomalies) == 1
        assert "111111" in report.anomalies[0] and "222222" in report.anomalies[0]
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).first() is None
        assert report.links_created_from_desc_text == 0

    async def test_short_number_prose_is_ignored_silently(
        self, db_session: AsyncSession
    ) -> None:
        """'pixiv 100 followers' shouldn't be mistaken for an id — the
        minimum-4-digit rule must skip it with no anomaly at all."""
        artist = Tags(
            title="PopularArtist",
            type=TagType.ARTIST,
            desc="pixiv 100 followers and rising",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        assert report.anomalies == []
        assert report.links_created_from_desc_text == 0
        assert (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
            )
        ).first() is None


@pytest.mark.integration
class TestTitleAndDescTextRespectEarlierSources:
    async def test_alias_derived_identity_skips_title_and_desc_text_harvest(
        self, db_session: AsyncSession
    ) -> None:
        """A tag that already has an identity from source 2 (alias title)
        must not have sources 4/5 fire on unrelated pixiv-looking text
        elsewhere on the same tag — no second link, no anomaly."""
        artist = Tags(
            title="AliasedArtist (Pixiv 999999)",
            type=TagType.ARTIST,
            desc="also pixiv 888888",
            usage_count=1,
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(Tags(title="Pixiv 12345", type=TagType.ARTIST, alias_of=artist.tag_id))
        await db_session.commit()

        report = await run_backfill(db_session, apply=True)

        links = (
            (
                await db_session.execute(
                    select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 1
        assert (links[0].site, links[0].external_id) == ("pixiv", "12345")
        assert report.links_created_from_aliases == 1
        assert report.links_created_from_titles == 0
        assert report.links_created_from_desc_text == 0
        assert report.anomalies == []


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
