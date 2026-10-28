"""
Tests for include_comments on GET /api/v1/images/ (bundled feed comments).

Design: docs/plans/2026-Q3/2026-08-17-bundled-feed-comments-design.md
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comments
from app.models.image import Images


async def _make_image(db_session: AsyncSession, sample_image_data: dict, filename: str) -> Images:
    image = Images(**{**sample_image_data, "filename": filename})
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestListImagesIncludeComments:
    """GET /api/v1/images/?include_comments=true bundles the page's comments."""

    async def test_comments_field_null_without_param(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        await _make_image(db_session, sample_image_data, "bundle-off-001")
        response = await client.get("/api/v1/images/")
        assert response.status_code == 200
        assert response.json()["comments"] is None

    async def test_comments_field_null_when_param_false(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        await _make_image(db_session, sample_image_data, "bundle-false-001")
        response = await client.get("/api/v1/images/", params={"include_comments": "false"})
        assert response.status_code == 200
        assert response.json()["comments"] is None

    async def test_bundles_page_comments_grouped_and_ordered(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        image_a = await _make_image(db_session, sample_image_data, "bundle-a-001")
        image_b = await _make_image(db_session, sample_image_data, "bundle-b-001")

        # Distinct explicit dates: the column server-defaults to now(), and
        # same-second inserts would make the order assertion nondeterministic.
        first = Comments(
            image_id=image_a.image_id,
            user_id=1,
            post_text="first",
            date=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        db_session.add(first)
        await db_session.commit()
        await db_session.refresh(first)

        db_session.add_all(
            [
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="reply to first",
                    parent_comment_id=first.post_id,
                    date=datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
                ),
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="second",
                    date=datetime(2026, 1, 1, 12, 0, 2, tzinfo=UTC),
                ),
                Comments(
                    image_id=image_a.image_id,
                    user_id=1,
                    post_text="deleted, must not appear",
                    deleted=True,
                    date=datetime(2026, 1, 1, 12, 0, 3, tzinfo=UTC),
                ),
            ]
        )
        await db_session.commit()

        # Default sort is image_id DESC, so the two fresh images lead page 1.
        response = await client.get(
            "/api/v1/images/", params={"include_comments": "true", "per_page": 100}
        )
        assert response.status_code == 200
        data = response.json()

        page_ids = {img["image_id"] for img in data["images"]}
        assert image_a.image_id in page_ids
        assert image_b.image_id in page_ids

        comments_map = data["comments"]
        assert comments_map is not None

        # JSON object keys are strings.
        bundled = comments_map[str(image_a.image_id)]
        assert [c["post_text"] for c in bundled] == ["first", "reply to first", "second"]

        # Thread linkage and the embedded author survive the bundling.
        assert bundled[1]["parent_comment_id"] == first.post_id
        assert all(c["user"]["username"] for c in bundled)

        # Comment-less images do not appear in the map.
        assert str(image_b.image_id) not in comments_map

    async def test_bundles_in_post_id_order_when_dates_tie(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        image = await _make_image(db_session, sample_image_data, "bundle-tie-001")

        # Identical explicit date: post_id (insertion order) must break the tie.
        tied_date = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        first = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="posted first",
            date=tied_date,
        )
        db_session.add(first)
        await db_session.commit()
        await db_session.refresh(first)

        second = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="posted second",
            date=tied_date,
        )
        db_session.add(second)
        await db_session.commit()
        await db_session.refresh(second)

        assert first.post_id < second.post_id

        response = await client.get(
            "/api/v1/images/", params={"include_comments": "true", "per_page": 1}
        )
        assert response.status_code == 200
        data = response.json()

        bundled = data["comments"][str(image.image_id)]
        assert [c["post_text"] for c in bundled] == ["posted first", "posted second"]

    async def test_empty_map_when_page_has_no_comments(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        image = await _make_image(db_session, sample_image_data, "bundle-empty-001")
        # per_page=1 with the default image_id DESC sort pins the page to the
        # image just created, so other tests' commented images can't leak in.
        response = await client.get(
            "/api/v1/images/", params={"include_comments": "true", "per_page": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["images"][0]["image_id"] == image.image_id
        assert data["comments"] == {}
