"""End-to-end ML tag suggestion workflow.

Drives the whole feature through the real API + real test DB, faking only the
two boundaries the rest of the suite already fakes: the ML inference service
(``_get_ml_service`` in the router) and the mapping/resolution resolvers in the
shared pipeline (``resolve_external_tags`` / ``resolve_tag_relationships``).
Everything else — suggestion storage, the review approve/reject path, TagLink
and TagHistory creation, the denormalized tag-type flag refresh, and the
regenerate-resets-removed-approvals behaviour — runs for real against the DB.

These complement the focused unit/API suites
(``tests/services/test_ml_suggestion_pipeline.py``,
``tests/api/v1/test_ml_tag_suggestions.py``) by exercising the full chain in
one flow rather than each link in isolation.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.models.user import Users

ROUTER = "app.api.v1.ml_tag_suggestions"
PIPELINE = "app.services.ml_suggestion_pipeline"


class FakeMLService:
    """Stand-in for MLTagSuggestionService at the inference boundary.

    The pipeline only ever calls ``generate_suggestions``; the model is never
    loaded because ``_get_ml_service`` is patched to return this instance.
    """

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions

    async def generate_suggestions(
        self, image_path: str, min_confidence: float = 0.35
    ) -> list[dict[str, Any]]:
        return list(self._predictions)


def _resolver_to_tag_ids(rows: list[dict[str, Any]]):
    """Build a fake resolve_external_tags returning the given tag_id rows."""

    async def _resolver(db, suggestions):
        return [dict(r) for r in rows]

    return _resolver


async def _passthrough_resolver(db, suggestions):
    return suggestions


async def _make_user(db_session: AsyncSession, suffix: str, salt: str = "testsalt12345678") -> Users:
    user = Users(
        username=f"wf_{suffix}",
        email=f"wf_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt=salt,
        active=1,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_image(db_session: AsyncSession, user: Users, suffix: str, tmp_path) -> Images:
    """Create an image row plus the local fullsize file the pipeline expects."""
    image = Images(
        filename=f"2024-01-01-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()

    fake_image = tmp_path / "fullsize" / f"{image.filename}.{image.ext}"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")
    return image


async def _generate_sync(
    client: AsyncClient,
    image_id: int,
    token: str,
    fake_service: FakeMLService,
    mapped: list[dict[str, Any]],
):
    """Drive the sync generate endpoint with the inference + resolver boundaries faked."""
    with (
        patch(f"{ROUTER}._get_ml_service", AsyncMock(return_value=fake_service)),
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        return await client.post(
            f"/api/v1/images/{image_id}/ml-tag-suggestions/generate?sync=true",
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.integration
class TestMlTagSuggestionWorkflow:
    """End-to-end integration tests for the ML tag suggestion system."""

    async def test_complete_workflow_generate_to_approval(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """Generate (sync) → GET → approve all → TagLinks + history + flag set.

        Ports the branch's complete-workflow test, then adds the approve-path
        invariants the old branch lacked: a TagHistory add row per approved
        theme tag and the image's has_theme flag flipping on.
        """
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "approve")
        # Theme tags (type=1) the resolver maps the predictions onto.
        tag1 = Tags(title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(title="short hair", type=1, user_id=user.user_id)
        tag3 = Tags(title="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.flush()

        image = await _make_image(db_session, user, "approve", tmp_path)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService(
            [
                {"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"},
                {"external_tag": "short_hair", "confidence": 0.88, "model_version": "v1"},
                {"external_tag": "blush", "confidence": 0.85, "model_version": "v1"},
            ]
        )
        mapped = [
            {"tag_id": tag1.tag_id, "confidence": 0.92, "model_version": "v1"},
            {"tag_id": tag2.tag_id, "confidence": 0.88, "model_version": "v1"},
            {"tag_id": tag3.tag_id, "confidence": 0.85, "model_version": "v1"},
        ]

        # Step 1: generate suggestions synchronously through the API.
        gen = await _generate_sync(client, image.image_id, token, fake, mapped)
        assert gen.status_code == 200
        assert gen.json()["suggestions_created"] == 3

        # Step 2: GET suggestions back, sorted by confidence desc.
        response = await client.get(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["image_id"] == image.image_id
        assert len(data["suggestions"]) == 3
        assert data["total"] == 3
        assert data["pending"] == 3
        assert data["approved"] == 0
        assert data["rejected"] == 0
        assert [s["confidence"] for s in data["suggestions"]] == [0.92, 0.88, 0.85]
        assert data["suggestions"][0]["model_version"] == "v1"

        # Step 3: approve all three.
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]
        review = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": sid, "action": "approve"} for sid in suggestion_ids
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review.status_code == 200
        review_data = review.json()
        assert review_data["approved"] == 3
        assert review_data["rejected"] == 0
        assert review_data["errors"] == []

        # Step 4: TagLinks created for all three tags, owned by the reviewer.
        links = (
            (await db_session.execute(select(TagLinks).where(TagLinks.image_id == image.image_id)))
            .scalars()
            .all()
        )
        assert {link.tag_id for link in links} == {tag1.tag_id, tag2.tag_id, tag3.tag_id}
        assert all(link.user_id == user.user_id for link in links)

        # Step 5: suggestions marked approved with reviewer + timestamp.
        suggestions = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
                )
            )
            .scalars()
            .all()
        )
        assert all(s.status == "approved" for s in suggestions)
        assert all(s.reviewed_by_user_id == user.user_id for s in suggestions)
        assert all(s.reviewed_at is not None for s in suggestions)

        # Step 6 (new invariant): one TagHistory add row (action="a") per tag.
        history = (
            (
                await db_session.execute(
                    select(TagHistory).where(
                        TagHistory.image_id == image.image_id,
                        TagHistory.action == "a",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {h.tag_id for h in history} == {tag1.tag_id, tag2.tag_id, tag3.tag_id}
        assert all(h.user_id == user.user_id for h in history)

        # Step 7 (new invariant): approving theme tags flips the has_theme flag.
        # Flags bypass the ORM identity map — re-query, don't read a stale object.
        has_theme = (
            await db_session.execute(
                select(Images.has_theme).where(Images.image_id == image.image_id)
            )
        ).scalar_one()
        assert has_theme == 1

    async def test_workflow_with_rejection(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """Rejecting suggestions creates no TagLinks and no history."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "reject")
        tag1 = Tags(title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(title="short hair", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2])
        await db_session.flush()

        image = await _make_image(db_session, user, "reject", tmp_path)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService(
            [
                {"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"},
                {"external_tag": "short_hair", "confidence": 0.88, "model_version": "v1"},
            ]
        )
        mapped = [
            {"tag_id": tag1.tag_id, "confidence": 0.92, "model_version": "v1"},
            {"tag_id": tag2.tag_id, "confidence": 0.88, "model_version": "v1"},
        ]

        gen = await _generate_sync(client, image.image_id, token, fake, mapped)
        assert gen.json()["suggestions_created"] == 2

        data = (
            await client.get(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]

        review = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": sid, "action": "reject"} for sid in suggestion_ids
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review.status_code == 200
        review_data = review.json()
        assert review_data["approved"] == 0
        assert review_data["rejected"] == 2

        links = (
            (await db_session.execute(select(TagLinks).where(TagLinks.image_id == image.image_id)))
            .scalars()
            .all()
        )
        assert links == []

        history = (
            (
                await db_session.execute(
                    select(TagHistory).where(TagHistory.image_id == image.image_id)
                )
            )
            .scalars()
            .all()
        )
        assert history == []

        suggestions = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
                )
            )
            .scalars()
            .all()
        )
        assert all(s.status == "rejected" for s in suggestions)

    async def test_workflow_mixed_approval_rejection(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """Only approved suggestions create TagLinks in a mixed batch."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "mixed")
        tag1 = Tags(title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(title="short hair", type=1, user_id=user.user_id)
        tag3 = Tags(title="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.flush()

        image = await _make_image(db_session, user, "mixed", tmp_path)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService(
            [
                {"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"},
                {"external_tag": "short_hair", "confidence": 0.88, "model_version": "v1"},
                {"external_tag": "blush", "confidence": 0.85, "model_version": "v1"},
            ]
        )
        mapped = [
            {"tag_id": tag1.tag_id, "confidence": 0.92, "model_version": "v1"},
            {"tag_id": tag2.tag_id, "confidence": 0.88, "model_version": "v1"},
            {"tag_id": tag3.tag_id, "confidence": 0.85, "model_version": "v1"},
        ]

        await _generate_sync(client, image.image_id, token, fake, mapped)

        data = (
            await client.get(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
        # Suggestions sorted by confidence desc → [tag1, tag2, tag3].
        suggestion_ids = [s["suggestion_id"] for s in data["suggestions"]]

        review = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [
                    {"suggestion_id": suggestion_ids[0], "action": "approve"},
                    {"suggestion_id": suggestion_ids[1], "action": "approve"},
                    {"suggestion_id": suggestion_ids[2], "action": "reject"},
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review.status_code == 200
        review_data = review.json()
        assert review_data["approved"] == 2
        assert review_data["rejected"] == 1

        links = (
            (await db_session.execute(select(TagLinks).where(TagLinks.image_id == image.image_id)))
            .scalars()
            .all()
        )
        assert {link.tag_id for link in links} == {tag1.tag_id, tag2.tag_id}
        assert tag3.tag_id not in {link.tag_id for link in links}

    async def test_workflow_non_owner_cannot_view_or_review(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """A non-owner without IMAGE_TAG_ADD is rejected on both GET and review."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        owner = await _make_user(db_session, "perm_owner")
        other = await _make_user(db_session, "perm_other", salt="testsalt87654321")
        tag = Tags(title="long hair", type=1, user_id=owner.user_id)
        db_session.add(tag)
        await db_session.flush()

        image = await _make_image(db_session, owner, "perm", tmp_path)
        await db_session.commit()

        owner_token = create_access_token(user_id=owner.user_id)
        fake = FakeMLService(
            [{"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"}]
        )
        mapped = [{"tag_id": tag.tag_id, "confidence": 0.92, "model_version": "v1"}]
        await _generate_sync(client, image.image_id, owner_token, fake, mapped)

        suggestion = (
            await db_session.execute(
                select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
            )
        ).scalar_one()

        other_token = create_access_token(user_id=other.user_id)

        get_resp = await client.get(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert get_resp.status_code == 403

        review_resp = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={
                "suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]
            },
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert review_resp.status_code == 403

        # The rejected review created nothing.
        links = (
            (await db_session.execute(select(TagLinks).where(TagLinks.image_id == image.image_id)))
            .scalars()
            .all()
        )
        assert links == []

    async def test_workflow_skips_existing_tags(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """A tag already linked to the image is not re-suggested."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "existing")
        tag1 = Tags(title="long hair", type=1, user_id=user.user_id)
        tag2 = Tags(title="short hair", type=1, user_id=user.user_id)
        tag3 = Tags(title="blush", type=1, user_id=user.user_id)
        db_session.add_all([tag1, tag2, tag3])
        await db_session.flush()

        image = await _make_image(db_session, user, "existing", tmp_path)
        await db_session.flush()

        # tag1 already on the image.
        db_session.add(TagLinks(image_id=image.image_id, tag_id=tag1.tag_id, user_id=user.user_id))
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService(
            [
                {"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"},
                {"external_tag": "short_hair", "confidence": 0.88, "model_version": "v1"},
                {"external_tag": "blush", "confidence": 0.85, "model_version": "v1"},
            ]
        )
        mapped = [
            {"tag_id": tag1.tag_id, "confidence": 0.92, "model_version": "v1"},
            {"tag_id": tag2.tag_id, "confidence": 0.88, "model_version": "v1"},
            {"tag_id": tag3.tag_id, "confidence": 0.85, "model_version": "v1"},
        ]

        gen = await _generate_sync(client, image.image_id, token, fake, mapped)
        # tag1 skipped (already linked) → only 2 suggestions created.
        assert gen.json()["suggestions_created"] == 2

        suggestions = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
                )
            )
            .scalars()
            .all()
        )
        assert {s.tag_id for s in suggestions} == {tag2.tag_id, tag3.tag_id}
        assert tag1.tag_id not in {s.tag_id for s in suggestions}

        data = (
            await client.get(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
        assert len(data["suggestions"]) == 2
        assert data["total"] == 2

    async def test_workflow_sync_generate_missing_file_returns_404(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """Sync generate over an image with no local file returns 404, stores nothing."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "missing")
        # Image row WITHOUT the fullsize file.
        image = Images(
            filename="2024-01-01-missing",
            ext="jpg",
            user_id=user.user_id,
            md5_hash="hash_missing",
            filesize=1024,
            width=800,
            height=600,
        )
        db_session.add(image)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService([])
        with patch(f"{ROUTER}._get_ml_service", AsyncMock(return_value=fake)):
            resp = await client.post(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions/generate?sync=true",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        suggestions = (
            (
                await db_session.execute(
                    select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
                )
            )
            .scalars()
            .all()
        )
        assert suggestions == []

    async def test_workflow_regenerate_resets_removed_tag_approval(
        self, client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch
    ):
        """Approve → remove the tag link → regenerate flips the approval back to pending.

        Exercises the full loop end-to-end: a suggestion approved through the
        API, its TagLink later removed, then a second sync generate that finds
        the tag missing and resets the approved row to pending.
        """
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        user = await _make_user(db_session, "regen")
        tag = Tags(title="long hair", type=1, user_id=user.user_id)
        db_session.add(tag)
        await db_session.flush()

        image = await _make_image(db_session, user, "regen", tmp_path)
        await db_session.commit()

        token = create_access_token(user_id=user.user_id)
        fake = FakeMLService(
            [{"external_tag": "long_hair", "confidence": 0.92, "model_version": "v1"}]
        )
        mapped = [{"tag_id": tag.tag_id, "confidence": 0.92, "model_version": "v1"}]

        # Generate → approve.
        await _generate_sync(client, image.image_id, token, fake, mapped)
        data = (
            await client.get(
                f"/api/v1/images/{image.image_id}/ml-tag-suggestions",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
        suggestion_id = data["suggestions"][0]["suggestion_id"]

        review = await client.post(
            f"/api/v1/images/{image.image_id}/ml-tag-suggestions/review",
            json={"suggestions": [{"suggestion_id": suggestion_id, "action": "approve"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review.json()["approved"] == 1

        suggestion = (
            await db_session.execute(
                select(MlTagSuggestions).where(
                    MlTagSuggestions.suggestion_id == suggestion_id
                )
            )
        ).scalar_one()
        assert suggestion.status == "approved"

        # Remove the TagLink the approval created (tag taken off the image).
        await db_session.execute(
            delete(TagLinks).where(
                TagLinks.image_id == image.image_id, TagLinks.tag_id == tag.tag_id
            )
        )
        await db_session.commit()

        # Regenerate: the model still predicts the tag, but it's no longer on the
        # image, so the approved suggestion resets to pending (0 new created).
        gen2 = await _generate_sync(client, image.image_id, token, fake, mapped)
        assert gen2.status_code == 200
        assert gen2.json()["suggestions_created"] == 0

        await db_session.refresh(suggestion)
        assert suggestion.status == "pending"
        assert suggestion.reviewed_at is None
        assert suggestion.reviewed_by_user_id is None
