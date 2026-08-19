"""Live scoring for the personalized /images/recommended feed.

Reads the nightly-precomputed user_tag_affinity profile and scores a capped,
recency-biased candidate set at request time (measured ≈49 ms for the heaviest
profile on production-scale data). Negative-affinity tags subtract from an
image's score, so the feed actively avoids content the user routinely
down-rates — not just fails to boost it.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from app.models.user import Users
from app.models.user_favorite import UserFavoriteLinks, UserFavoriteTags
from app.models.user_tag_affinity import UserTagAffinity
from app.schemas.image import FavoriteAttribution, TagSummary
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES


@dataclass
class RecommendationPage:
    total: int
    image_ids: list[int]  # this page's ids, score-ordered
    because: dict[int, list[TagSummary]] = field(default_factory=dict)
    profile_ready: bool = False


# Within one favorite's list the recency bias is deliberately sharper than the
# affinity sample's decay: a favorite has <= TASTE_FAV_PER_FAVORITE_CAP recent
# matches and fresh ones should lead, while still rotating.
_FAV_RECENCY_DECAY = 0.97


@dataclass
class FavoritePool:
    attribution: FavoriteAttribution
    image_ids: list[int]  # newest-first, already visibility/seen-filtered


def _weighted_shuffle(ids: list[int], rng: random.Random, decay: float) -> list[int]:
    """Rank-weighted shuffle (Efraimidis–Spirakis): item at rank r has weight
    decay**r and sorting by u**(1/w) descending favours early ranks while
    rotating with the rng. Computed via the exact Gumbel-max equivalent
    rank*ln(decay) - ln(-ln(u)) — the same permutation for the same draws
    (both sort E/w ascending, E = -ln(u)), but the rank term is linear so it
    never under- or overflows at any rank or decay. decay→0 degenerates to
    the input order, decay=1.0 to a uniform shuffle."""
    ln_decay = math.log(decay)
    keyed = []
    for rank, iid in enumerate(ids):
        u = max(rng.random(), 1e-300)  # random() may return exactly 0.0
        keyed.append((rank * ln_decay - math.log(-math.log(u)), iid))
    keyed.sort(reverse=True)
    return [iid for _, iid in keyed]


def compose_day_list(
    affinity_ids: list[int],
    favorite_pools: list[FavoritePool],
    rng: random.Random,
    *,
    feed_size: int,
    fav_share: float,
    affinity_decay: float,
) -> tuple[list[int], dict[int, FavoriteAttribution]]:
    """Deterministic given the rng: the day's feed order plus per-image favorite
    attribution. An image listed by any favorite belongs to the favorites side —
    it is claimed by the lowest-position favorite listing it and removed from the
    affinity ranking (cross-pool dedupe; favorites attribution wins). Each slot
    draws favorites with probability fav_share (round-robin across favorites)
    while any favorites remain; an exhausted side yields its slots to the other.
    """
    attribution: dict[int, FavoriteAttribution] = {}
    fav_lists: list[list[int]] = []
    for pool in favorite_pools:
        fresh = [iid for iid in pool.image_ids if iid not in attribution]
        for iid in fresh:
            attribution[iid] = pool.attribution
        if fresh:
            fav_lists.append(_weighted_shuffle(fresh, rng, _FAV_RECENCY_DECAY))
    affinity_order = _weighted_shuffle(
        [iid for iid in affinity_ids if iid not in attribution], rng, affinity_decay
    )

    day_list: list[int] = []
    affinity_next = 0
    fav_cursor = 0
    while len(day_list) < feed_size and (fav_lists or affinity_next < len(affinity_order)):
        draw_favorite = bool(fav_lists) and (
            affinity_next >= len(affinity_order) or rng.random() < fav_share
        )
        if draw_favorite:
            current = fav_lists[fav_cursor % len(fav_lists)]
            day_list.append(current.pop(0))
            if not current:
                fav_lists.remove(current)
            fav_cursor += 1
        else:
            day_list.append(affinity_order[affinity_next])
            affinity_next += 1
    return day_list, {iid: attribution[iid] for iid in day_list if iid in attribution}


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


def _visibility_sql(show_all: bool, hide_reposts: bool) -> tuple[str, str, dict[str, Any]]:
    """The status/repost filter fragments both feed queries embed (alias ``i``)."""
    status_clause = "" if show_all else "AND i.status IN :public_statuses"
    hide_reposts_clause = "AND i.status != :repost_status" if hide_reposts else ""
    params: dict[str, Any] = {}
    if not show_all:
        params["public_statuses"] = list(PUBLIC_IMAGE_STATUSES)
    if hide_reposts:
        params["repost_status"] = int(ImageStatus.REPOST)
    return status_clause, hide_reposts_clause, params


async def load_favorite_pools(db: AsyncSession, user: Users, *, cap: int) -> list[FavoritePool]:
    """The user's favorites as per-favorite recent-match lists, ordered combos
    (by position) then favorite tags (sources before artists, by position).
    Recall is per favorite so the composer's round-robin can keep one prolific
    favorite from monopolising the share; the seen/visibility filter runs once
    over the union. Matching alias-expands stored (canonical) tag ids."""
    combo_rows = (
        await db.execute(
            select(  # type: ignore[call-overload]
                CharacterSourceLinks.character_tag_id, CharacterSourceLinks.source_tag_id
            )
            .join(UserFavoriteLinks, UserFavoriteLinks.link_id == CharacterSourceLinks.id)
            .where(UserFavoriteLinks.user_id == user.user_id)
            .order_by(UserFavoriteLinks.position)
        )
    ).all()
    tag_rows = (
        await db.execute(
            select(UserFavoriteTags.tag_id)  # type: ignore[call-overload]
            .join(Tags, Tags.tag_id == UserFavoriteTags.tag_id)
            .where(UserFavoriteTags.user_id == user.user_id)
            .order_by(Tags.type, UserFavoriteTags.position)
        )
    ).all()
    if not combo_rows and not tag_rows:
        return []

    referenced = {tid for row in combo_rows for tid in row} | {r[0] for r in tag_rows}
    summaries = {
        r.tag_id: TagSummary(tag_id=r.tag_id, title=r.title, type=r.type)
        for r in (
            await db.execute(
                select(Tags.tag_id, Tags.title, Tags.type).where(  # type: ignore[call-overload]
                    Tags.tag_id.in_(referenced)  # type: ignore[union-attr]
                )
            )
        ).all()
    }
    expand: dict[int, list[int]] = {tid: [tid] for tid in referenced}
    for r in (
        await db.execute(
            select(Tags.tag_id, Tags.alias_of).where(  # type: ignore[call-overload]
                Tags.alias_of.in_(referenced)  # type: ignore[union-attr]
            )
        )
    ).all():
        expand[r.alias_of].append(r.tag_id)

    tag_recall = text(
        """
        SELECT DISTINCT tl.image_id FROM tag_links tl
        WHERE tl.tag_id IN :ids ORDER BY tl.image_id DESC LIMIT :cap
        """
    ).bindparams(bindparam("ids", expanding=True))
    combo_recall = text(
        """
        SELECT DISTINCT tl1.image_id
        FROM tag_links tl1 JOIN tag_links tl2 ON tl2.image_id = tl1.image_id
        WHERE tl1.tag_id IN :char_ids AND tl2.tag_id IN :src_ids
        ORDER BY tl1.image_id DESC LIMIT :cap
        """
    ).bindparams(bindparam("char_ids", expanding=True), bindparam("src_ids", expanding=True))

    recalled: list[tuple[FavoriteAttribution, list[int]]] = []
    for char_id, src_id in combo_rows:
        rows = (
            await db.execute(
                combo_recall,
                {"char_ids": expand[char_id], "src_ids": expand[src_id], "cap": cap},
            )
        ).all()
        recalled.append(
            (
                FavoriteAttribution(character=summaries[char_id], source=summaries[src_id]),
                [r.image_id for r in rows],
            )
        )
    for (tag_id,) in tag_rows:
        rows = (await db.execute(tag_recall, {"ids": expand[tag_id], "cap": cap})).all()
        recalled.append((FavoriteAttribution(tag=summaries[tag_id]), [r.image_id for r in rows]))

    all_ids = {iid for _, ids in recalled for iid in ids}
    if not all_ids:
        return [FavoritePool(attribution=a, image_ids=[]) for a, _ in recalled]
    status_clause, hide_reposts_clause, params = _visibility_sql(
        user.show_all_images == 1, user.hide_reposts == 1
    )
    filter_stmt = text(
        f"""
        SELECT i.image_id FROM images i
        WHERE i.image_id IN :ids
          AND i.user_id != :uid
          {status_clause}
          {hide_reposts_clause}
          AND NOT EXISTS (
              SELECT 1 FROM favorites f
              WHERE f.user_id = :uid AND f.image_id = i.image_id)
          AND NOT EXISTS (
              SELECT 1 FROM image_ratings r
              WHERE r.user_id = :uid AND r.image_id = i.image_id)
        """
    ).bindparams(bindparam("ids", expanding=True))
    allowed = {
        r.image_id
        for r in (
            await db.execute(filter_stmt, {"ids": list(all_ids), "uid": user.user_id, **params})
        ).all()
    }
    return [
        FavoritePool(attribution=a, image_ids=[iid for iid in ids if iid in allowed])
        for a, ids in recalled
    ]
