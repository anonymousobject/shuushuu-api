"""Live scoring for the personalized /images/recommended feed.

Reads the nightly-precomputed user_tag_affinity profile and scores a capped,
recency-biased candidate set at request time (measured ≈49 ms for the heaviest
profile on production-scale data). Negative-affinity tags subtract from an
image's score, so the feed actively avoids content the user routinely
down-rates — not just fails to boost it.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.tag import Tags
from app.models.user import Users
from app.models.user_tag_affinity import UserTagAffinity
from app.schemas.image import TagSummary
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES


@dataclass
class RecommendationPage:
    total: int
    image_ids: list[int]  # this page's ids, score-ordered
    because: dict[int, list[TagSummary]] = field(default_factory=dict)
    profile_ready: bool = False


async def get_recommended_images(
    db: AsyncSession, user: Users, *, page: int, per_page: int
) -> RecommendationPage:
    """Score candidates against the user's profile; return one page of image ids.

    Pipeline: top-K positive-affinity tags -> recency-biased candidate images
    carrying any of them (capped) -> sum affinity over ALL profile-covered tags
    (alias-resolved; DISTINCT guards against alias+canonical double links) ->
    drop seen (favorited/rated/own) and invisible images -> order by score,
    keep the top TASTE_FEED_POOL, slice the requested page.
    """
    top_rows = (
        await db.execute(
            select(UserTagAffinity.tag_id)  # type: ignore[call-overload]
            .where(UserTagAffinity.user_id == user.user_id, UserTagAffinity.affinity > 0)
            .order_by(UserTagAffinity.affinity.desc())  # type: ignore[attr-defined]
            .limit(settings.TASTE_TOP_TAGS)
        )
    ).all()
    top_tag_ids = [r[0] for r in top_rows]
    if not top_tag_ids:
        # Distinguish "no profile" (cold start) from "profile exists but has no
        # positive tags" (e.g. a user who only down-rates) — the frontend shows
        # different copy for each.
        has_rows = (
            await db.execute(
                select(UserTagAffinity.user_id)  # type: ignore[call-overload]
                .where(UserTagAffinity.user_id == user.user_id)
                .limit(1)
            )
        ).first()
        return RecommendationPage(total=0, image_ids=[], profile_ready=has_rows is not None)

    # The candidate subquery below filters tag_links.tag_id directly (not via
    # COALESCE(tg.alias_of, tg.tag_id) like the scoring/because queries) so
    # it can use the tag_links tag_id index. That means an image tagged only
    # through an alias of a top-affinity canonical tag would never reach
    # scoring at all. Fix: expand top_tag_ids with any alias tags pointing at
    # them (one extra indexed lookup on tags.alias_of) and use the expanded
    # set only for candidate selection — scoring/because already resolve
    # aliases via COALESCE and don't need it.
    alias_rows = (
        await db.execute(
            select(Tags.tag_id).where(Tags.alias_of.in_(top_tag_ids))  # type: ignore[call-overload,union-attr]
        )
    ).all()
    candidate_tag_ids = top_tag_ids + [r[0] for r in alias_rows]

    show_all = user.show_all_images == 1
    status_clause = "" if show_all else "AND i.status IN :public_statuses"
    hide_reposts_clause = "AND i.status != :repost_status" if user.hide_reposts == 1 else ""

    sql = f"""
        SELECT d.image_id, SUM(d.affinity) AS score
        FROM (
            SELECT DISTINCT c.image_id, p.tag_id, p.affinity
            FROM (
                SELECT DISTINCT tl.image_id FROM tag_links tl
                WHERE tl.tag_id IN :candidate_tag_ids
                ORDER BY tl.image_id DESC
                LIMIT {int(settings.TASTE_CANDIDATE_CAP)}
            ) c
            JOIN images i ON i.image_id = c.image_id
            JOIN tag_links tl2 ON tl2.image_id = c.image_id
            JOIN tags tg ON tg.tag_id = tl2.tag_id
            JOIN user_tag_affinity p
              ON p.user_id = :uid AND p.tag_id = COALESCE(tg.alias_of, tg.tag_id)
            WHERE i.user_id != :uid
              {status_clause}
              {hide_reposts_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM favorites f
                  WHERE f.user_id = :uid AND f.image_id = c.image_id)
              AND NOT EXISTS (
                  SELECT 1 FROM image_ratings r
                  WHERE r.user_id = :uid AND r.image_id = c.image_id)
        ) d
        GROUP BY d.image_id
        ORDER BY score DESC, d.image_id DESC
        LIMIT {int(settings.TASTE_FEED_POOL)}
    """
    stmt = text(sql).bindparams(bindparam("candidate_tag_ids", expanding=True))
    params: dict[str, Any] = {"candidate_tag_ids": candidate_tag_ids, "uid": user.user_id}
    if not show_all:
        stmt = stmt.bindparams(bindparam("public_statuses", expanding=True))
        params["public_statuses"] = list(PUBLIC_IMAGE_STATUSES)
    if user.hide_reposts == 1:
        params["repost_status"] = int(ImageStatus.REPOST)

    scored = (await db.execute(stmt, params)).all()
    total = len(scored)
    offset = (page - 1) * per_page
    page_ids = [r.image_id for r in scored[offset : offset + per_page]]
    if not page_ids:
        return RecommendationPage(total=total, image_ids=[], profile_ready=True)

    # top contributing (positive) profile tags per page image
    because_stmt = text(
        """
        SELECT DISTINCT tl.image_id, p.tag_id, p.affinity, t.title, t.type
        FROM tag_links tl
        JOIN tags tg0 ON tg0.tag_id = tl.tag_id
        JOIN user_tag_affinity p
          ON p.user_id = :uid AND p.tag_id = COALESCE(tg0.alias_of, tg0.tag_id)
        JOIN tags t ON t.tag_id = p.tag_id
        WHERE tl.image_id IN :ids AND p.affinity > 0
        """
    ).bindparams(bindparam("ids", expanding=True))
    because_rows = (await db.execute(because_stmt, {"uid": user.user_id, "ids": page_ids})).all()
    by_image: dict[int, list[Any]] = {}
    for row in because_rows:
        by_image.setdefault(row.image_id, []).append(row)
    because = {
        iid: [
            TagSummary(tag_id=r.tag_id, title=r.title, type=r.type)
            for r in sorted(rows, key=lambda r: -r.affinity)[:3]
        ]
        for iid, rows in by_image.items()
    }
    return RecommendationPage(total=total, image_ids=page_ids, because=because, profile_ready=True)
