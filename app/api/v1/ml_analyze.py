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
from app.services.ml_suggestion_pipeline import (  # light: no onnxruntime
    filter_character_suggestions,
    filter_superseded_parents,
)
from app.services.rate_limit import check_analyze_rate_limit
from app.services.tag_mapping_service import resolve_external_tags

logger = get_logger(__name__)

router = APIRouter(prefix="/ml-tag-suggestions", tags=["ml-tag-suggestions"])


def _downscale_for_inference(content: bytes, max_edge: int) -> bytes:
    """Return image bytes with the longest edge bounded to ``max_edge``.

    Already-small images are returned unchanged (no recompression). Larger ones
    are decoded — JPEGs at a reduced libjpeg scale via ``draft()`` so the full
    resolution is never materialised (cheap, and no decompression bomb) — then
    downscaled and re-encoded as JPEG. Raises on an undecodable or pathologically
    huge non-JPEG image (the caller maps that to 400).
    """
    with Image.open(io.BytesIO(content)) as img:
        if max(img.size) <= max_edge:
            return content
        img.draft("RGB", (max_edge, max_edge))  # JPEG: decode at 1/2..1/8; no-op otherwise
        rgb = img.convert("RGB")  # decode happens here (reduced for JPEG via draft)
    rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=95)
    return out.getvalue()


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
        inference_bytes = _downscale_for_inference(content, settings.ML_ANALYZE_DOWNSCALE_EDGE)
    except Exception as exc:  # undecodable, or a pathologically huge non-JPEG (bomb)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unprocessable image",
        ) from exc

    suffix = os.path.splitext(file.filename or "")[1] or ".img"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(inference_bytes)
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

    # Character gate: must run before parent-supersede so a gated character
    # child can't first supersede a theme parent (both chips would be lost).
    resolved = await filter_character_suggestions(db, resolved)

    # Drop a suggested parent when a confident suggested child supersedes it.
    resolved = await filter_superseded_parents(
        db, resolved, settings.ML_PARENT_SUPERSEDE_MIN_CONFIDENCE
    )

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
            (conf, AnalyzedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type, confidence=conf))
        )

    suggestions: list[AnalyzedTag] = []
    for _type, rows in by_type.items():
        rows.sort(key=lambda x: x[0], reverse=True)
        suggestions.extend(at for _conf, at in rows[: settings.ML_ANALYZE_MAX_SUGGESTIONS])
    return AnalyzeTagsResponse(suggestions=suggestions)
