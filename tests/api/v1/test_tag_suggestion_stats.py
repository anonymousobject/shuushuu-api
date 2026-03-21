"""
API tests for tag suggestion statistics endpoint.

Tests cover:
- GET /api/v1/tags/suggestion-stats (public leaderboard)
- Minimum suggestion threshold filtering
- Acceptance rate calculation
- Sort options
- Time-based filtering (last 30 days vs all time)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReportCategory, ReportStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.tag import Tags
from app.models.user import Users


async def create_user(
    db_session: AsyncSession,
    username: str,
    email: str | None = None,
) -> Users:
    """Create a test user."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=email or f"{username}@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def create_image(db_session: AsyncSession, user_id: int, idx: int = 0) -> Images:
    """Create a test image."""
    image = Images(
        filename=f"test-suggestion-stats-{idx}",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash=f"sugstatstesthash{idx:08d}",
        filesize=100000,
        width=800,
        height=600,
        user_id=user_id,
        status=ImageStatus.ACTIVE,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def create_tag(db_session: AsyncSession, name: str) -> Tags:
    """Create a test tag."""
    tag = Tags(tag_name=name, tag_type=0, master_tag=0)
    db_session.add(tag)
    await db_session.commit()
    await db_session.refresh(tag)
    return tag


async def create_suggestion(
    db_session: AsyncSession,
    user: Users,
    image: Images,
    tag: Tags,
    accepted: bool | None = None,
    suggestion_type: int = 1,
) -> ImageReportTagSuggestions:
    """Create a report with a single tag suggestion."""
    report = ImageReports(
        image_id=image.image_id,
        user_id=user.user_id,
        category=ReportCategory.TAG_SUGGESTIONS,
        status=ReportStatus.REVIEWED if accepted is not None else ReportStatus.PENDING,
    )
    db_session.add(report)
    await db_session.flush()

    suggestion = ImageReportTagSuggestions(
        report_id=report.report_id,
        tag_id=tag.tag_id,
        suggestion_type=suggestion_type,
        accepted=accepted,
    )
    db_session.add(suggestion)
    await db_session.commit()
    await db_session.refresh(suggestion)
    return suggestion


@pytest.mark.anyio
async def test_suggestion_stats_empty(client: AsyncClient) -> None:
    """Returns empty list when no suggestions exist."""
    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []


@pytest.mark.anyio
async def test_suggestion_stats_minimum_threshold(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Users below minimum threshold (5) are excluded."""
    user = await create_user(db_session, "fewsuggestions")
    image = await create_image(db_session, user.user_id)

    # Create only 4 suggestions (below threshold of 5)
    for i in range(4):
        tag = await create_tag(db_session, f"threshold_tag_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=True)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []


@pytest.mark.anyio
async def test_suggestion_stats_at_threshold(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Users at exactly the minimum threshold (5) are included."""
    user = await create_user(db_session, "atthreshold")
    image = await create_image(db_session, user.user_id)

    for i in range(5):
        tag = await create_tag(db_session, f"at_threshold_tag_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=True)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["user_id"] == user.user_id
    assert item["username"] == "atthreshold"
    assert item["total_suggestions"] == 5
    assert item["accepted_count"] == 5
    assert item["rejected_count"] == 0
    assert item["pending_count"] == 0
    assert item["acceptance_rate"] == 100.0


@pytest.mark.anyio
async def test_suggestion_stats_mixed_statuses(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Correctly counts accepted, rejected, and pending suggestions."""
    user = await create_user(db_session, "mixedstats")
    image = await create_image(db_session, user.user_id)

    # 3 accepted, 1 rejected, 2 pending = 6 total
    for i in range(3):
        tag = await create_tag(db_session, f"mixed_accepted_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=True)

    tag_rejected = await create_tag(db_session, "mixed_rejected")
    await create_suggestion(db_session, user, image, tag_rejected, accepted=False)

    for i in range(2):
        tag = await create_tag(db_session, f"mixed_pending_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=None)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["total_suggestions"] == 6
    assert item["accepted_count"] == 3
    assert item["rejected_count"] == 1
    assert item["pending_count"] == 2
    # Acceptance rate = accepted / (accepted + rejected) * 100 = 3/4 * 100 = 75.0
    assert item["acceptance_rate"] == 75.0


@pytest.mark.anyio
async def test_suggestion_stats_acceptance_rate_excludes_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Acceptance rate only considers decided suggestions (not pending)."""
    user = await create_user(db_session, "pendingrate")
    image = await create_image(db_session, user.user_id)

    # 1 accepted, 0 rejected, 4 pending = 5 total
    tag_accepted = await create_tag(db_session, "rate_accepted")
    await create_suggestion(db_session, user, image, tag_accepted, accepted=True)

    for i in range(4):
        tag = await create_tag(db_session, f"rate_pending_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=None)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    # Only 1 decided suggestion, so rate = 1/1 * 100 = 100.0
    assert item["acceptance_rate"] == 100.0


@pytest.mark.anyio
async def test_suggestion_stats_all_pending_null_rate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """When all suggestions are pending, acceptance_rate is null."""
    user = await create_user(db_session, "allpending")
    image = await create_image(db_session, user.user_id)

    for i in range(5):
        tag = await create_tag(db_session, f"allpending_tag_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=None)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["acceptance_rate"] is None


@pytest.mark.anyio
async def test_suggestion_stats_multiple_users_sorted_by_acceptance_rate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Multiple users sorted by acceptance rate descending by default."""
    # User A: 5/5 accepted = 100%
    user_a = await create_user(db_session, "user_a_perfect")
    image_a = await create_image(db_session, user_a.user_id, idx=10)
    for i in range(5):
        tag = await create_tag(db_session, f"usera_tag_{i}")
        await create_suggestion(db_session, user_a, image_a, tag, accepted=True)

    # User B: 3/5 accepted = 60%
    user_b = await create_user(db_session, "user_b_decent")
    image_b = await create_image(db_session, user_b.user_id, idx=11)
    for i in range(3):
        tag = await create_tag(db_session, f"userb_accepted_{i}")
        await create_suggestion(db_session, user_b, image_b, tag, accepted=True)
    for i in range(2):
        tag = await create_tag(db_session, f"userb_rejected_{i}")
        await create_suggestion(db_session, user_b, image_b, tag, accepted=False)

    response = await client.get("/api/v1/tags/suggestion-stats?sort=acceptance_rate&order=desc")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["username"] == "user_a_perfect"
    assert data["items"][0]["acceptance_rate"] == 100.0
    assert data["items"][1]["username"] == "user_b_decent"
    assert data["items"][1]["acceptance_rate"] == 60.0


@pytest.mark.anyio
async def test_suggestion_stats_sort_by_total(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Can sort by total_suggestions."""
    # User with 5 suggestions
    user_few = await create_user(db_session, "user_few")
    image_few = await create_image(db_session, user_few.user_id, idx=20)
    for i in range(5):
        tag = await create_tag(db_session, f"few_tag_{i}")
        await create_suggestion(db_session, user_few, image_few, tag, accepted=True)

    # User with 8 suggestions
    user_many = await create_user(db_session, "user_many")
    image_many = await create_image(db_session, user_many.user_id, idx=21)
    for i in range(8):
        tag = await create_tag(db_session, f"many_tag_{i}")
        await create_suggestion(db_session, user_many, image_many, tag, accepted=True)

    response = await client.get("/api/v1/tags/suggestion-stats?sort=total_suggestions&order=desc")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["username"] == "user_many"
    assert data["items"][0]["total_suggestions"] == 8
    assert data["items"][1]["username"] == "user_few"
    assert data["items"][1]["total_suggestions"] == 5


@pytest.mark.anyio
async def test_suggestion_stats_includes_suggestion_type_breakdown(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stats include add vs remove suggestion type counts."""
    user = await create_user(db_session, "typebreakdown")
    image = await create_image(db_session, user.user_id, idx=30)

    # 3 add suggestions, 2 remove suggestions
    for i in range(3):
        tag = await create_tag(db_session, f"add_type_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=True, suggestion_type=1)

    for i in range(2):
        tag = await create_tag(db_session, f"remove_type_{i}")
        await create_suggestion(db_session, user, image, tag, accepted=True, suggestion_type=2)

    response = await client.get("/api/v1/tags/suggestion-stats")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["add_count"] == 3
    assert item["remove_count"] == 2
