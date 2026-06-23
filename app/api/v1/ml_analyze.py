"""Stateless upload-form tag analysis: infer + map tags for image bytes."""

import hashlib
import io
import json
import os
import tempfile
from typing import Annotated, Any

import redis.asyncio as redis
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import VerifiedUser
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.tag import Tags
from app.schemas.ml_analyze import AnalyzedTag, AnalyzeTagsResponse
from app.services.ml_categories import SUGGESTION_CATEGORIES  # light: no onnxruntime
from app.services.ml_runtime import get_ml_service, inference_slot
from app.services.rate_limit import check_analyze_rate_limit
from app.services.tag_mapping_service import resolve_external_tags

logger = get_logger(__name__)

router = APIRouter(prefix="/ml-tag-suggestions", tags=["ml-tag-suggestions"])


@router.post("/analyze", response_model=AnalyzeTagsResponse)
async def analyze_image_tags(
    current_user: VerifiedUser,
    file: Annotated[UploadFile, File(description="Image file to analyze")],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: AsyncSession = Depends(get_db),
) -> AnalyzeTagsResponse:
    if not settings.ML_TAG_SUGGESTIONS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ML tag suggestions are disabled",
        )

    await check_analyze_rate_limit(current_user.id, redis_client)

    content = await file.read()
    if len(content) > settings.MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large"
        )
    md5 = hashlib.md5(content).hexdigest()

    try:
        with Image.open(io.BytesIO(content)) as probe:
            width, height = probe.size
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image file"
        ) from exc
    if max(width, height) > settings.ML_ANALYZE_MAX_DIMENSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image dimensions too large to analyze",
        )

    suffix = os.path.splitext(file.filename or "")[1] or ".img"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        ml_service = await get_ml_service()
        async with inference_slot():
            raw = await ml_service.generate_raw_predictions(
                tmp_path,
                include_categories=SUGGESTION_CATEGORIES,
                # Infer at the STORAGE floor so the md5 cache holds the complete set the
                # post-upload worker will persist (cache-hit must match cache-miss, which
                # stores at ML_MIN_CONFIDENCE). The higher upload-form DISPLAY floor
                # (ML_ANALYZE_MIN_CONFIDENCE) is applied in _resolve_to_response.
                min_confidence=settings.ML_MIN_CONFIDENCE,
            )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    try:
        await redis_client.set(
            f"ml:analyze:{md5}",
            json.dumps(raw),
            ex=settings.ML_ANALYZE_CACHE_TTL_SECONDS,
        )
    except Exception:  # cache is best-effort; never fail analyze on a Redis error
        logger.warning("ml_analyze_cache_write_failed", md5=md5, exc_info=True)

    return await _resolve_to_response(db, raw)


async def _resolve_to_response(db: AsyncSession, raw: list[dict[str, Any]]) -> AnalyzeTagsResponse:
    """Map raw external preds -> internal tags, attach title/type, sort+floor+cap per type."""
    resolved = await resolve_external_tags(db, raw)  # [{tag_id, confidence, model_version}]
    if not resolved:
        return AnalyzeTagsResponse(suggestions=[])

    # Keep the best confidence per tag_id (resolve can collapse aliases to one id).
    best: dict[int, float] = {}
    for r in resolved:
        tid = r["tag_id"]
        best[tid] = max(best.get(tid, 0.0), r["confidence"])

    tags_result = await db.execute(select(Tags).where(Tags.tag_id.in_(list(best))))  # type: ignore[union-attr]
    tags_by_id = {t.tag_id: t for t in tags_result.scalars().all()}

    # NOTE: this floor is on the MAPPING-SCALED confidence (resolve_external_tags
    # multiplied each by mapping.confidence), so it is NOT redundant with the
    # pre-mapping inference floor. Tags with a NULL title are dropped (unusable as a chip).
    by_type: dict[int, list[tuple[float, AnalyzedTag]]] = {}
    for tid, conf in best.items():
        tag = tags_by_id.get(tid)
        if (
            tag is None
            or tag.tag_id is None
            or not tag.title
            or conf < settings.ML_ANALYZE_MIN_CONFIDENCE
        ):
            continue
        by_type.setdefault(tag.type, []).append(
            (conf, AnalyzedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type))
        )

    suggestions: list[AnalyzedTag] = []
    for _type, rows in by_type.items():
        rows.sort(key=lambda x: x[0], reverse=True)
        suggestions.extend(at for _conf, at in rows[: settings.ML_ANALYZE_MAX_SUGGESTIONS])
    return AnalyzeTagsResponse(suggestions=suggestions)
