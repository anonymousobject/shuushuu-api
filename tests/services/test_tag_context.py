"""Query-count guards for stamp_context_sources.

The module documents "at most two small indexed queries per request (alias map
+ links), skipped when the page has no character tags". These tests hold it to
that: a page carrying source tags but zero character tags must not touch the
database at all, since the stamping rule has nothing to stamp onto.
"""

from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from app.schemas.image import ImageDetailedResponse, TagSummary
from app.services.tag_context import stamp_context_sources


def _response(image_id: int, tags: list[TagSummary]) -> ImageDetailedResponse:
    return ImageDetailedResponse(
        image_id=image_id,
        user_id=1,
        ext="jpg",
        date_added=datetime(2026, 1, 1, tzinfo=UTC),
        locked=0,
        posts=0,
        favorites=0,
        bayesian_rating=0.0,
        num_ratings=0,
        medium=0,
        large=0,
        tags=tags,
    )


def _summary(tag_id: int, type_id: int) -> TagSummary:
    return TagSummary(tag_id=tag_id, title=f"tag {tag_id}", type=type_id)


class _ExecuteCounter:
    """Count ORM executions issued through a session for the test's duration."""

    def __init__(self, db: AsyncSession) -> None:
        self._session = db.sync_session
        self.count = 0

    def __enter__(self) -> _ExecuteCounter:
        event.listen(self._session, "do_orm_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info: object) -> None:
        event.remove(self._session, "do_orm_execute", self._on_execute)

    def _on_execute(self, orm_execute_state: object) -> None:
        self.count += 1


async def test_source_only_page_issues_no_queries(db_session: AsyncSession):
    """Sources without characters can never be stamped — don't ask the database."""
    responses = [_response(9001, [_summary(901, TagType.SOURCE)])]

    with _ExecuteCounter(db_session) as counter:
        await stamp_context_sources(db_session, responses)

    assert counter.count == 0
    assert responses[0].tags is not None
    assert responses[0].tags[0].context_source_tag_id is None


async def test_tagless_page_issues_no_queries(db_session: AsyncSession):
    responses = [_response(9002, []), _response(9003, None)]

    with _ExecuteCounter(db_session) as counter:
        await stamp_context_sources(db_session, responses)

    assert counter.count == 0


async def test_character_page_stamps_within_two_queries(db_session: AsyncSession):
    db_session.add(Tags(tag_id=911, type=TagType.SOURCE, title="ctx q src"))
    db_session.add(Tags(tag_id=912, type=TagType.CHARACTER, title="ctx q char"))
    await db_session.flush()
    db_session.add(CharacterSourceLinks(character_tag_id=912, source_tag_id=911))
    await db_session.flush()

    responses = [_response(9004, [_summary(912, TagType.CHARACTER), _summary(911, TagType.SOURCE)])]

    with _ExecuteCounter(db_session) as counter:
        await stamp_context_sources(db_session, responses)

    assert counter.count == 2
    assert responses[0].tags is not None
    assert responses[0].tags[0].context_source_tag_id == 911
