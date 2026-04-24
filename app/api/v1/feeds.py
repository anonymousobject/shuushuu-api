"""Atom feed endpoints."""

from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.services.feeds import (
    FeedMeta,
    build_atom_feed,
    compute_feed_etag,
    fetch_feed_entries,
    fetch_feed_sentinel,
    newest_timestamp,
)

router = APIRouter(tags=["feeds"])

CACHE_CONTROL = "public, max-age=300"
ATOM_CONTENT_TYPE = "application/atom+xml; charset=utf-8"


def _frontend(*parts: str) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return "/".join([base, *parts])


def _self_url(request: Request) -> str:
    """Absolute URL of the current request — used for feed <link rel='self'>."""
    return str(request.url).split("?")[0]


def _ensure_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC — the DB stores naive timestamps for date_added."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_not_modified(request: Request, etag: str, last_mod: datetime | None) -> bool:
    """Conditional-request evaluation."""
    inm = request.headers.get("if-none-match", "").strip()
    if inm == "*" or (inm and inm == etag):
        return True

    if last_mod is not None:
        ims = request.headers.get("if-modified-since")
        if ims:
            try:
                ims_dt = parsedate_to_datetime(ims)
            except (TypeError, ValueError):
                ims_dt = None
            if ims_dt is not None and ims_dt >= _ensure_utc(last_mod):
                return True

    return False


def _cacheable_headers(etag: str, last_mod: datetime | None) -> dict[str, str]:
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if last_mod is not None:
        headers["Last-Modified"] = format_datetime(_ensure_utc(last_mod), usegmt=True)
    return headers


@router.get("/images.atom")
async def list_images_atom(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Latest 50 active images, newest first."""
    sentinel = await fetch_feed_sentinel(db, tag_ids=None, limit=50)
    etag = compute_feed_etag(sentinel)
    last_mod = newest_timestamp(sentinel)
    headers = _cacheable_headers(etag, last_mod)

    if _is_not_modified(request, etag, last_mod):
        return Response(status_code=304, headers=headers)

    entries = await fetch_feed_entries(db, tag_ids=None, limit=50)
    meta = FeedMeta(
        feed_id="tag:e-shuushuu.net,2005:feed:images",
        title="Shuushuu — latest images",
        self_url=_self_url(request),
        alternate_url=_frontend(),
    )
    xml = build_atom_feed(meta, entries)

    return Response(content=xml, media_type=ATOM_CONTENT_TYPE, headers=headers)
