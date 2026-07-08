"""Schemas for the URL-import endpoints."""

from pydantic import BaseModel, field_validator


class UrlResolveRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        if len(v) > 2000:
            raise ValueError("URL too long")
        return v


class ResolvedImageOut(BaseModel):
    token: str
    thumb_token: str | None = None
    width: int | None = None
    height: int | None = None


class UrlResolveResponse(BaseModel):
    site: str
    canonical_url: str
    title: str | None = None
    artist_name: str | None = None
    images: list[ResolvedImageOut]
