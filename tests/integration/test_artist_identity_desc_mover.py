"""Integration tests for the artist-identity desc mover.

Follow-up to the backfill (`test_artist_identity_backfill.py`, same fixture
style): the backfill copies pixiv identity out of a desc into a link but
leaves the desc text untouched. This mover moves the copy instead of
duplicating it -- see docs/plans/2026-08-01-external-artist-identity-design.md
and the mods' explicit ask ("shame if we can't move the links").
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagAuditActionType, TagType
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity_desc_mover import run_desc_mover


@pytest.mark.integration
class TestDescMoverUrlDifferentFromLink:
    async def test_url_desc_moved_and_link_rewritten_to_verbatim(
        self, db_session: AsyncSession
    ) -> None:
        """The tag owns the identity via a modern-form link (e.g. created by
        the backfill), but the desc still carries the legacy URL text mods
        may have archived. The link's URL must be rewritten to that verbatim
        legacy string -- not the other way around -- so the archived form
        survives."""
        artist = Tags(
            title="OldArtist",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://www.pixiv.net/users/1000121",
            site="pixiv",
            external_id="1000121",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        assert artist.desc == "クロネコ"
        assert link.url == "http://www.pixiv.net/member.php?id=1000121"
        assert report.descs_cleaned == 1
        assert report.links_rewritten_to_verbatim == 1
        assert report.anomalies == []

    async def test_audit_row_recorded_for_desc_change(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="OldArtist",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        await run_desc_mover(db_session, apply=True)

        audit_rows = (
            (
                await db_session.execute(
                    select(TagAuditLog).where(TagAuditLog.tag_id == artist.tag_id)
                )
            )
            .scalars()
            .all()
        )
        # Only the desc change is audited (DESCRIPTION_CHANGE fits exactly);
        # no existing TagAuditActionType cleanly represents a link's *url*
        # being rewritten (LINK_ARCHIVE_CHANGED is specifically archive_url),
        # so the link rewrite itself is not separately audited -- see report.
        assert len(audit_rows) == 1
        assert audit_rows[0].action_type == TagAuditActionType.DESCRIPTION_CHANGE
        assert audit_rows[0].old_desc == "クロネコ / http://www.pixiv.net/member.php?id=1000121"
        assert audit_rows[0].new_desc == "クロネコ"
        assert audit_rows[0].user_id is None

    async def test_leading_separator_dangling_is_tidied(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="Artist2",
            type=TagType.ARTIST,
            desc="https://pixiv.net/member.php?id=100022 / 佐",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://pixiv.net/member.php?id=100022",
                site="pixiv",
                external_id="100022",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "佐"
        assert report.descs_cleaned == 1
        # URL identical to the link already -- no rewrite needed.
        assert report.links_rewritten_to_verbatim == 0

    async def test_doubled_mid_string_separator_is_collapsed(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="Artist3",
            type=TagType.ARTIST,
            desc="佐 / https://pixiv.net/member.php?id=100022 / more info",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://pixiv.net/member.php?id=100022",
                site="pixiv",
                external_id="100022",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "佐 / more info"
        assert report.descs_cleaned == 1

    async def test_trailing_query_params_preserved_in_verbatim_rewrite(
        self, db_session: AsyncSession
    ) -> None:
        """DESC_URL_PATTERN only needs the id digits to parse identity, but
        the mover has to move the FULL pasted URL. A trailing '&ref=abc' the
        id-focused pattern doesn't capture must not be truncated off the
        verbatim string that lands on the link, nor left behind as garbage
        in the desc."""
        artist = Tags(
            title="OldArtist5",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121&ref=abc",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://www.pixiv.net/users/1000121",
            site="pixiv",
            external_id="1000121",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        assert artist.desc == "クロネコ"
        assert link.url == "http://www.pixiv.net/member.php?id=1000121&ref=abc"
        assert report.links_rewritten_to_verbatim == 1


@pytest.mark.integration
class TestDescMoverUrlIdenticalToLink:
    async def test_identical_url_strips_desc_without_rewriting_link(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="佐",
            type=TagType.ARTIST,
            desc="佐 / https://pixiv.net/member.php?id=100022",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://pixiv.net/member.php?id=100022",
            site="pixiv",
            external_id="100022",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        assert artist.desc == "佐"
        assert link.url == "https://pixiv.net/member.php?id=100022"
        assert report.descs_cleaned == 1
        assert report.links_rewritten_to_verbatim == 0
        # No rewrite happened, so the sample shouldn't claim a pending one.
        assert report.samples[0].link_url_before is None
        assert report.samples[0].link_url_after is None


@pytest.mark.integration
class TestDescMoverUrlOnlyDesc:
    async def test_url_only_desc_becomes_null(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="OldArtist2",
            type=TagType.ARTIST,
            desc="http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/member.php?id=1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.descs_cleaned == 1
        assert report.descs_emptied == 1

    async def test_url_only_desc_with_trailing_params_becomes_null(
        self, db_session: AsyncSession
    ) -> None:
        """A URL-only desc with a query string the id-focused pattern
        doesn't capture must still empty to NULL, not leave '&ref=abc'
        garbage behind."""
        artist = Tags(
            title="OldArtist6",
            type=TagType.ARTIST,
            desc="http://www.pixiv.net/member.php?id=1000121&ref=abc",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/member.php?id=1000121&ref=abc",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.descs_emptied == 1

    async def test_url_only_desc_with_trailing_period_becomes_null(
        self, db_session: AsyncSession
    ) -> None:
        """A trailing '.' right-trimmed off the URL span by
        _expand_url_span lands, unconsumed, in the text immediately after
        the span -- it must not survive as an orphaned '.' where the desc
        should have gone fully to NULL."""
        artist = Tags(
            title="OrphanPunctPeriod",
            type=TagType.ARTIST,
            desc="http://www.pixiv.net/users/1000121.",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.descs_emptied == 1
        assert report.anomalies == []

    async def test_url_only_desc_with_trailing_exclamation_becomes_null(
        self, db_session: AsyncSession
    ) -> None:
        """Same as the trailing-period case but with '!'."""
        artist = Tags(
            title="OrphanPunctExclamation",
            type=TagType.ARTIST,
            desc="http://www.pixiv.net/users/1000121!",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.descs_emptied == 1
        assert report.anomalies == []

    async def test_url_only_desc_with_trailing_question_mark_becomes_null(
        self, db_session: AsyncSession
    ) -> None:
        """Same as the trailing-period case but with a lone '?' (no query
        string following it -- distinct from the confident '?ref=abc'
        case, which is real query content and stays on the link)."""
        artist = Tags(
            title="OrphanPunctQuestion",
            type=TagType.ARTIST,
            desc="http://www.pixiv.net/users/1000121?",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.descs_emptied == 1
        assert report.anomalies == []

    async def test_orphaned_trailing_period_after_dangling_separator_is_tidied(
        self, db_session: AsyncSession
    ) -> None:
        """'クロネコ / <url>.' combines two cleanup rules: the trailing
        period is an orphaned _expand_url_span trim artifact, and once it's
        normalized away the '/' in front of it is a delimiter dangling at a
        (now) true edge -- both must resolve to a single clean 'クロネコ',
        not 'クロネコ / .'."""
        artist = Tags(
            title="OrphanPunctAfterSlash",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121.",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "クロネコ"
        assert report.descs_cleaned == 1
        assert report.anomalies == []


@pytest.mark.integration
class TestDescMoverDuplicateUrlGuard:
    async def test_verbatim_rewrite_skipped_when_url_already_exists_on_tag(
        self, db_session: AsyncSession
    ) -> None:
        """The tag already keeps both URL forms as separate link rows (a
        common mod pattern for archive reasons). Rewriting the identity
        link's URL to the desc's legacy form would collide with
        unique_tag_url -- skip the rewrite, never delete either link, but
        still strip the now-redundant desc text."""
        artist = Tags(
            title="OldArtist3",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        identity_link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://www.pixiv.net/users/1000121",
            site="pixiv",
            external_id="1000121",
        )
        archival_link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add_all([identity_link, archival_link])
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(identity_link)
        links = (
            (
                await db_session.execute(
                    select(TagExternalLinks).where(TagExternalLinks.tag_id == artist.tag_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 2
        assert identity_link.url == "https://www.pixiv.net/users/1000121"
        assert artist.desc == "クロネコ"
        assert report.descs_cleaned == 1
        assert report.links_rewritten_to_verbatim == 0
        assert report.links_verbatim_skipped_duplicate == 1


@pytest.mark.integration
class TestDescMoverIdentityNotOwned:
    async def test_identity_owned_by_another_tag_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        owner = Tags(title="RealOwner", type=TagType.ARTIST, usage_count=1)
        confused = Tags(
            title="ConfusedArtist",
            type=TagType.ARTIST,
            desc="see https://www.pixiv.net/users/42",
        )
        db_session.add_all([owner, confused])
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=owner.tag_id,
                url="https://www.pixiv.net/users/42",
                site="pixiv",
                external_id="42",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(confused)
        assert confused.desc == "see https://www.pixiv.net/users/42"
        assert report.descs_cleaned == 0
        assert any("42" in a and str(confused.tag_id) in a for a in report.anomalies)

    async def test_identity_unclaimed_by_anyone_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="NoLinkYet",
            type=TagType.ARTIST,
            desc="https://www.pixiv.net/users/999",
        )
        db_session.add(artist)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "https://www.pixiv.net/users/999"
        assert report.descs_cleaned == 0
        assert any("999" in a for a in report.anomalies)


@pytest.mark.integration
class TestDescMoverMultipleIdentities:
    async def test_two_distinct_urls_is_an_anomaly(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="ConfusedArtist2",
            type=TagType.ARTIST,
            desc="https://www.pixiv.net/users/1 https://www.pixiv.net/users/2",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add_all(
            [
                TagExternalLinks(
                    tag_id=artist.tag_id,
                    url="https://www.pixiv.net/users/1",
                    site="pixiv",
                    external_id="1",
                ),
                TagExternalLinks(
                    tag_id=artist.tag_id,
                    url="https://www.pixiv.net/users/2",
                    site="pixiv",
                    external_id="2",
                ),
            ]
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "https://www.pixiv.net/users/1 https://www.pixiv.net/users/2"
        assert len(report.anomalies) == 1
        assert "1" in report.anomalies[0] and "2" in report.anomalies[0]

    async def test_same_identity_mentioned_twice_is_an_anomaly(
        self, db_session: AsyncSession
    ) -> None:
        """A URL mention plus a separate bare-text mention of the *same* id
        is still two occurrences in the desc text -- the mover strips a
        single span, so picking one and leaving the other would be a silent
        half-fix. Conservative: flag rather than guess."""
        artist = Tags(
            title="RepeatedMention",
            type=TagType.ARTIST,
            desc="pixiv 1000121, also https://www.pixiv.net/users/1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/users/1000121",
                site="pixiv",
                external_id="1000121",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "pixiv 1000121, also https://www.pixiv.net/users/1000121"
        assert len(report.anomalies) == 1
        assert report.descs_cleaned == 0
        assert report.bare_text_stripped == 0


@pytest.mark.integration
class TestDescMoverBareText:
    async def test_bare_text_stripped_when_tag_owns_identity(
        self, db_session: AsyncSession
    ) -> None:
        artist = Tags(
            title="SomeArtist",
            type=TagType.ARTIST,
            desc="find me at pixiv 97567 thanks",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/users/97567",
                site="pixiv",
                external_id="97567",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "find me at thanks"
        assert report.bare_text_stripped == 1
        assert report.links_rewritten_to_verbatim == 0
        assert report.anomalies == []

    async def test_bare_text_only_desc_becomes_null(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="BareOnly",
            type=TagType.ARTIST,
            desc="pixiv 97567",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/users/97567",
                site="pixiv",
                external_id="97567",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc is None
        assert report.bare_text_stripped == 1
        assert report.descs_emptied == 1


@pytest.mark.integration
class TestDescMoverAmbiguousRemnant:
    async def test_dangling_label_remnant_is_an_anomaly(self, db_session: AsyncSession) -> None:
        """'profile: <url>' isn't one of the documented '/'-or-'|' separator
        conventions -- the mover doesn't know how to confidently drop the
        now-dangling 'profile:' label, so it must flag rather than guess."""
        artist = Tags(
            title="LabeledArtist",
            type=TagType.ARTIST,
            desc="profile: https://www.pixiv.net/member.php?id=555",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="https://www.pixiv.net/member.php?id=555",
                site="pixiv",
                external_id="555",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "profile: https://www.pixiv.net/member.php?id=555"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]

    async def test_mid_string_label_remnant_is_flagged_same_as_position_zero(
        self, db_session: AsyncSession
    ) -> None:
        """The same 'profile:' label dangling mid-string (not at the very
        start of the desc) must be flagged exactly like the position-0 case
        above -- the remnant check has to look at the text immediately
        touching the removed span, not just the whole desc's global
        boundaries."""
        artist = Tags(
            title="MidStringLabel",
            type=TagType.ARTIST,
            desc="見て profile: http://www.pixiv.net/member.php?id=555 desu",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/member.php?id=555",
                site="pixiv",
                external_id="555",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "見て profile: http://www.pixiv.net/member.php?id=555 desu"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]

    async def test_empty_parens_remnant_is_flagged(self, db_session: AsyncSession) -> None:
        """A URL that was itself wrapped in parens leaves an empty '()'
        behind -- collapsing it would require guessing whether the outer
        text should be joined with or without a space, so it's flagged."""
        artist = Tags(
            title="ParenWrapped",
            type=TagType.ARTIST,
            desc="アーティスト(http://www.pixiv.net/users/123)prof",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/123",
                site="pixiv",
                external_id="123",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "アーティスト(http://www.pixiv.net/users/123)prof"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]


@pytest.mark.integration
class TestDescMoverSymmetricDelimiterCollapse:
    async def test_doubled_comma_separator_is_collapsed(self, db_session: AsyncSession) -> None:
        """A comma on both sides of the removed URL (a list-style aside) is
        an unambiguous doubling -- confidently collapse to one, unlike the
        label-colon case above.

        DB-backed regression case: an earlier version of _expand_url_span
        treated the trailing comma as URL-plausible and baked it into
        link.url ('.../users/123,') -- assert the persisted link URL is
        exactly clean, not just the desc.
        """
        artist = Tags(
            title="CommaArtist",
            type=TagType.ARTIST,
            desc="hello, http://www.pixiv.net/users/123, world",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="http://www.pixiv.net/users/123",
            site="pixiv",
            external_id="123",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        assert artist.desc == "hello, world"
        assert link.url == "http://www.pixiv.net/users/123"
        assert report.descs_cleaned == 1
        assert report.anomalies == []

    async def test_doubled_dash_separator_is_collapsed(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="DashArtist",
            type=TagType.ARTIST,
            desc="see - http://www.pixiv.net/users/123 - end",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/123",
                site="pixiv",
                external_id="123",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "see - end"
        assert report.descs_cleaned == 1
        assert report.anomalies == []


@pytest.mark.integration
class TestDescMoverUrlSpanExpansionSafety:
    async def test_non_ascii_text_glued_to_url_is_not_absorbed(
        self, db_session: AsyncSession
    ) -> None:
        """CJK text glued directly onto the URL with zero separator on
        either side, on both ends, is a case _strip_and_tidy can't
        confidently resolve (joining would guess a separator that clearly
        wasn't there). The regression under test is narrower and more
        important than the anomaly outcome itself: DESC_URL_PATTERN's
        non-ASCII-unaware expansion must never pull 'です' into link.url --
        assert that regardless of which desc/anomaly path is taken."""
        artist = Tags(
            title="GluedCJK",
            type=TagType.ARTIST,
            desc="見てhttp://www.pixiv.net/users/456です",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="http://www.pixiv.net/users/456",
            site="pixiv",
            external_id="456",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        # The link was already clean and must stay exactly that -- never
        # corrupted with the glued-on "です".
        assert link.url == "http://www.pixiv.net/users/456"
        # Conservative choice: glued-on-both-sides content is ambiguous
        # enough to flag rather than guess how to join "見て" and "です", so
        # nothing is written at all (desc stays exactly as it was).
        assert artist.desc == "見てhttp://www.pixiv.net/users/456です"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]

    async def test_glued_after_side_only_is_flagged_not_joined_with_a_space(
        self, db_session: AsyncSession
    ) -> None:
        """Only the *after* side is glued (no whitespace between the id
        digits and 'です'); the *before* side has a real space. Joining
        would still fabricate a separator on the glued side that was never
        there -- must flag, not silently produce 'see です'."""
        artist = Tags(
            title="GluedAfterOnly",
            type=TagType.ARTIST,
            desc="see http://www.pixiv.net/users/123です",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/123",
                site="pixiv",
                external_id="123",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "see http://www.pixiv.net/users/123です"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]

    async def test_glued_before_side_only_is_flagged_not_joined_with_a_space(
        self, db_session: AsyncSession
    ) -> None:
        """Symmetric case: only the *before* side is glued (no whitespace
        between '見て' and the URL); 'end' follows with a real space. Must
        flag, not silently produce '見て end'."""
        artist = Tags(
            title="GluedBeforeOnly",
            type=TagType.ARTIST,
            desc="見てhttp://www.pixiv.net/users/123 end",
        )
        db_session.add(artist)
        await db_session.flush()
        db_session.add(
            TagExternalLinks(
                tag_id=artist.tag_id,
                url="http://www.pixiv.net/users/123",
                site="pixiv",
                external_id="123",
            )
        )
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        assert artist.desc == "見てhttp://www.pixiv.net/users/123 end"
        assert report.descs_cleaned == 0
        assert len(report.anomalies) == 1
        assert str(artist.tag_id) in report.anomalies[0]

    async def test_question_mark_query_string_is_confident_and_moves_cleanly(
        self, db_session: AsyncSession
    ) -> None:
        """'?' is the standard query-string separator -- excluding it from
        _CONFIDENT_URL_TAIL_CHARS was a regression (round 1 handled this
        correctly; round 2's tightening over-corrected). The CJK/prose
        protection comes from the non-ASCII stop and whitespace boundary,
        not from excluding '?' -- a whitespace-separated desc with a real
        '?ref=abc' query string must still move the full URL onto the
        link."""
        artist = Tags(
            title="QueryStringArtist",
            type=TagType.ARTIST,
            desc="見て http://www.pixiv.net/users/123?ref=abc 見て",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://www.pixiv.net/users/123",
            site="pixiv",
            external_id="123",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=True)

        await db_session.refresh(artist)
        await db_session.refresh(link)
        assert artist.desc == "見て 見て"
        assert link.url == "http://www.pixiv.net/users/123?ref=abc"
        assert report.links_rewritten_to_verbatim == 1
        assert report.anomalies == []


@pytest.mark.integration
class TestDescMoverDryRun:
    async def test_dry_run_writes_nothing(self, db_session: AsyncSession) -> None:
        artist = Tags(
            title="OldArtist4",
            type=TagType.ARTIST,
            desc="クロネコ / http://www.pixiv.net/member.php?id=1000121",
        )
        db_session.add(artist)
        await db_session.flush()
        link = TagExternalLinks(
            tag_id=artist.tag_id,
            url="https://www.pixiv.net/users/1000121",
            site="pixiv",
            external_id="1000121",
        )
        db_session.add(link)
        await db_session.commit()

        report = await run_desc_mover(db_session, apply=False)

        # Report reflects what *would* happen, including the link-side change
        # (the sample must cover both, not just the desc) ...
        assert report.descs_cleaned == 1
        assert report.links_rewritten_to_verbatim == 1
        assert len(report.samples) == 1
        assert report.samples[0].before == "クロネコ / http://www.pixiv.net/member.php?id=1000121"
        assert report.samples[0].after == "クロネコ"
        assert report.samples[0].link_url_before == "https://www.pixiv.net/users/1000121"
        assert report.samples[0].link_url_after == "http://www.pixiv.net/member.php?id=1000121"

        # ...but nothing was actually written. Re-fetch fresh rows rather
        # than trusting the in-session objects (which a stray flush could
        # have already mutated).
        refetched_artist = (
            await db_session.execute(select(Tags).where(Tags.tag_id == artist.tag_id))
        ).scalar_one()
        refetched_link = (
            await db_session.execute(
                select(TagExternalLinks).where(TagExternalLinks.link_id == link.link_id)
            )
        ).scalar_one()
        assert refetched_artist.desc == "クロネコ / http://www.pixiv.net/member.php?id=1000121"
        assert refetched_link.url == "https://www.pixiv.net/users/1000121"

        audit_count = (
            await db_session.execute(select(TagAuditLog).where(TagAuditLog.tag_id == artist.tag_id))
        ).first()
        assert audit_count is None
