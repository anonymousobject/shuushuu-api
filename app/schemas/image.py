"""
Pydantic schemas for Image endpoints
"""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.config import TagType, settings
from app.core.r2_constants import PUBLIC_IMAGE_STATUSES_FOR_R2, R2Location
from app.models.image import ImageBase, VariantStatus
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.comment import CommentResponse
from app.schemas.common import UserSummary

# Sort order for tags in image responses: artist → source → character → theme
TAG_TYPE_SORT_ORDER = {
    TagType.ARTIST: 0,
    TagType.SOURCE: 1,
    TagType.CHARACTER: 2,
    TagType.THEME: 3,
}


def sort_tag_links_for_display(tag_links: list) -> list:  # type: ignore[type-arg]
    """Order an image's tag_links for display: by tag type (artist → source →
    character → theme) then alphabetically by title.

    The canonical order for surfacing an image's applied tags. Used by both
    ImageDetailedResponse and the ML suggestion grid so the tag list reads the
    same everywhere (board, detail page, and /ml-suggestions hover popup).
    """
    return sorted(
        tag_links,
        key=lambda tl: (
            TAG_TYPE_SORT_ORDER.get(tl.tag.type, 99),  # Primary: type order
            (tl.tag.title or "").lower(),  # Secondary: alphabetical
        ),
    )


def _cdn_eligible(status: int, r2_location: int) -> bool:
    """True when a direct-CDN URL can be emitted for these storage fields.

    All three must hold: R2 enabled, status publicly-viewable, and the
    canonical object in the public bucket. A mismatch falls back to the
    protected path, which routes on current r2_location.
    """
    return (
        settings.R2_ENABLED
        and status in PUBLIC_IMAGE_STATUSES_FOR_R2
        and r2_location == R2Location.PUBLIC
    )


def thumbnail_url_for(filename: str | None, status: int, r2_location: int) -> str:
    """Thumbnail URL (always WebP). Shared by ImageResponse and tag embeds."""
    if _cdn_eligible(status, r2_location):
        return f"{settings.R2_PUBLIC_CDN_URL}/thumbs/{filename}.webp"
    return f"{settings.IMAGE_BASE_URL}/thumbs/{filename}.webp"


class TagSummary(BaseModel):
    """Minimal tag info for embedding"""

    tag_id: int
    tag: str = Field(alias="title")  # Maps from Tags.title
    type_id: int = Field(alias="type")  # Maps from Tags.type
    usage_count: int = 0  # Needed by feed title composer; non-breaking.

    # Set only on character-type entries when the image carries EXACTLY ONE
    # source linked to this character (character_source_links) — the
    # contextual compound-search rule; None otherwise. Stamped post-build by
    # app.services.tag_context.stamp_context_sources.
    context_source_tag_id: int | None = None

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    # Cache reverse mapping from type_id to friendly name
    _TYPE_NAME_MAP = {
        getattr(TagType, attr_name): attr_name.replace("_", " ").title()
        for attr_name in dir(TagType)
        if not attr_name.startswith("_")
    }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def type_name(self) -> str:
        """Map type_id to friendly tag type name using TagType constant names"""
        return self._TYPE_NAME_MAP.get(self.type_id, "Unknown")


class ImageCreate(ImageBase):
    """Schema for creating a new image"""

    user_id: int


class ImageUpdate(BaseModel):
    """Schema for updating image metadata and owner status — all fields optional."""

    caption: str | None = None
    miscmeta: str | None = None
    status: int | None = None
    replacement_id: int | None = None

    @field_validator("caption")
    @classmethod
    def sanitize_caption(cls, v: str | None) -> str | None:
        """
        Sanitize image caption.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class ImageResponse(ImageBase):
    """
    Schema for image response - what API returns.

    Inherits public fields from ImageBase and adds additional public metadata.
    Does NOT include internal fields like IP, user agent, etc.
    """

    image_id: int
    user_id: int
    user: UserSummary | None = None  # Embedded user data (optional, loaded with selectinload)
    date_added: UTCDatetime
    locked: int
    posts: int
    favorites: int
    bayesian_rating: float
    num_ratings: int
    medium: int
    large: int
    replacement_id: int | None = None  # Original image ID when this is a repost (status=-1)
    r2_location: int = R2Location.NONE  # Tri-state: 0=NONE, 1=PUBLIC, 2=PRIVATE

    def _should_use_cdn(self) -> bool:
        """True when we can emit a direct-CDN URL for this image.

        All three must hold: R2 enabled, status is publicly-viewable, and the
        canonical object lives in the public bucket. A mismatch (e.g., public
        status but PRIVATE location during a bucket move) falls back to the
        /images/ path, which the endpoint routes based on current r2_location.
        """
        return _cdn_eligible(self.status, self.r2_location)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Fullsize URL — direct CDN when eligible, else protected path."""
        if self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/fullsize/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/images/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str:
        """Thumbnail URL (always WebP)."""
        return thumbnail_url_for(self.filename, self.status, self.r2_location)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def medium_url(self) -> str | None:
        """Medium variant (1280px edge) URL, or None if variant is absent."""
        if not self.medium:
            return None
        if self.medium == VariantStatus.READY and self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/medium/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/medium/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def large_url(self) -> str | None:
        """Large variant (2048px edge) URL, or None if variant is absent."""
        if not self.large:
            return None
        if self.large == VariantStatus.READY and self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/large/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/large/{self.filename}.{self.ext}"


class ImageDetailedResponse(ImageResponse):
    """
    Schema for detailed image response with extra metadata.

    Extends ImageResponse with:
    - Embedded user summary
    - Embedded tags
    """

    user: UserSummary | None = None  # Embedded user data (optional, loaded with selectinload)
    tags: list[TagSummary] | None = None  # Embedded tags (optional, loaded with selectinload)
    is_favorited: bool = False  # Whether the current user has favorited this image
    user_rating: int | None = None  # The rating given by the current user (if any)
    prev_image_id: int | None = None  # ID of the previous image (chronological)
    next_image_id: int | None = None  # ID of the next image (chronological)
    has_open_report: bool = False  # Mod-only (REPORT_VIEW): a pending report exists
    # Moderation reason for the current (hidden) status — owner + mods only, so the
    # red status band can explain why an image was taken down without a comment.
    reason_category: int | None = None
    status_reason: str | None = None
    # Tagger/admin-only: number of pending ML tag suggestions for this image.
    # None for anonymous users and users without IMAGE_TAG_ADD (or admin).
    ml_suggestion_count: int | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db_model(
        cls,
        image: Any,
        is_favorited: bool = False,
        user_rating: int | None = None,
        prev_image_id: int | None = None,
        next_image_id: int | None = None,
        has_open_report: bool = False,
        can_see_reason: bool = False,
    ) -> Self:
        """Create response from database model with relationships.

        ``can_see_reason`` gates the moderation reason (owner + mods); when False the
        reason fields stay null so a normal viewer never sees why an image is hidden.
        """
        data = ImageResponse.model_validate(image).model_dump()

        # Add user if loaded (groups come from User.groups property via eager-loaded user_groups)
        if hasattr(image, "user") and image.user:
            data["user"] = UserSummary.model_validate(image.user)

        # Add tags if loaded through tag_links, sorted by type then alphabetically
        if hasattr(image, "tag_links") and image.tag_links:
            sorted_links = sort_tag_links_for_display(image.tag_links)
            data["tags"] = [TagSummary.model_validate(tl.tag) for tl in sorted_links]

        data["is_favorited"] = is_favorited
        data["user_rating"] = user_rating
        data["prev_image_id"] = prev_image_id
        data["next_image_id"] = next_image_id
        data["has_open_report"] = has_open_report

        if can_see_reason:
            data["reason_category"] = getattr(image, "reason_category", None)
            data["status_reason"] = getattr(image, "status_reason", None)

        return cls(**data)


class ImageWithRatingResponse(ImageDetailedResponse):
    """An image plus the rating the subject user gave it.

    Mirrors ``UserWithRatingResponse`` (app/schemas/user.py), which serves the
    opposite direction of the same relation. ``subject_rating`` defaults to 0 —
    outside the valid 1-10 range — because ``from_db_model`` has no rating
    parameter, so the endpoint assigns it after construction the way
    ``list_images`` assigns ``ml_suggestion_count``.

    The name is ``subject_rating`` for two reasons. It cannot be ``rating``:
    ``ImageBase.rating`` is already the image's own average, inherited here as a
    float, and redeclaring it would drop the average from the response. It
    cannot be ``user_rating`` either: that means "the rating *you* gave"
    everywhere else, so a moderator would read the subject's score under a name
    that says "yours". "Subject" is right in both cases — the user whose list
    this is, who is the viewer when self and someone else when a moderator.
    """

    subject_rating: int = 0
    rated_at: UTCDatetimeOptional = None


class UserRatingsListResponse(BaseModel):
    """Schema for a paginated list of images a user has rated."""

    total: int
    page: int
    per_page: int
    images: list[ImageWithRatingResponse]


class ImageListResponse(BaseModel):
    """Schema for paginated image list with basic image data"""

    total: int
    page: int
    per_page: int
    images: list[ImageResponse]


class ImageDetailedListResponse(BaseModel):
    """Schema for paginated image list with detailed image data (includes relationships)"""

    total: int
    page: int
    per_page: int
    images: list[ImageDetailedResponse]
    # Populated only when the request sets include_comments=true: every
    # non-deleted comment for the returned images, oldest first, keyed by
    # image id. Images without comments are absent from the map.
    comments: dict[int, list[CommentResponse]] | None = None


class ImageUploadResponse(BaseModel):
    """Schema for image upload response"""

    message: str
    image_id: int
    image: ImageResponse


class ImageSearchParams(BaseModel):
    """Schema for image search parameters"""

    tags: str | None = None
    user_id: int | None = None
    sort_by: str = "image_id"
    sort_order: str = "DESC"
    page: int = 1
    per_page: int = 20


class ImageTagItem(BaseModel):
    """Schema for a single tag on an image"""

    tag_id: int
    tag: str
    type_id: int


class ImageTagsResponse(BaseModel):
    """Schema for image tags response"""

    image_id: int
    tags: list[ImageTagItem]


class ImageHashSearchResponse(BaseModel):
    """Schema for hash search response"""

    md5_hash: str
    found: int
    images: list[ImageResponse]


class ImageStatsResponse(BaseModel):
    """Schema for image statistics response"""

    total_images: int
    total_favorites: int
    average_rating: float


class BookmarkPageResponse(BaseModel):
    """Schema for bookmark page calculation response.

    Returns the page number where the user's bookmark appears based on
    their sort preferences and visibility settings.
    """

    page: int | None = Field(
        description="Page number (1-indexed) where bookmark appears, "
        "or null if bookmark is not visible under user's settings"
    )
    image_id: int = Field(description="The bookmarked image ID")
    images_per_page: int = Field(description="User's images_per_page setting")


class SimilarImageResult(ImageResponse):
    """Schema for a similar image result from IQDB.

    Extends ImageResponse with similarity score.
    """

    similarity_score: float = Field(description="Similarity score from IQDB (0-100)")


class SimilarImagesResponse(BaseModel):
    """Schema for similar images search response."""

    query_image_id: int = Field(description="The image ID that was searched")
    similar_images: list[SimilarImageResult] = Field(
        description="List of similar images ordered by similarity score (highest first)"
    )


class SimilarImagesUploadResponse(BaseModel):
    """Schema for similarity check by upload response.

    Unlike SimilarImagesResponse, has no query_image_id since the
    uploaded image is not stored in the database.
    """

    similar_images: list[SimilarImageResult] = Field(
        description="List of similar images ordered by similarity score (highest first)"
    )


class ImageUploadSimilarResponse(BaseModel):
    """Schema for 409 response when similar images are found during upload.

    Returned when IQDB detects near-duplicate images above IQDB_UPLOAD_THRESHOLD.
    Frontend should display these to the user for confirmation before retrying.
    """

    message: str
    similar_images: list[SimilarImageResult]


class ImageUploadDuplicateResponse(BaseModel):
    """Schema for 409 response when an exact duplicate is found during upload.

    Returned when the uploaded file's MD5 hash matches an image already on the
    board. ``detail`` is a human-readable message; ``existing_image_id`` lets the
    frontend link the user straight to the image that already exists.
    """

    detail: str
    existing_image_id: int


class FavoriteAttribution(BaseModel):
    """The user's favorite that drew a feed image: exactly one of ``tag``
    (source/artist favorite) or ``character``+``source`` (combo favorite) is set."""

    tag: TagSummary | None = None
    character: TagSummary | None = None
    source: TagSummary | None = None


class RecommendedImageResponse(ImageDetailedResponse):
    """A recommended image plus the profile tags that most contributed to its score."""

    because_tags: list[TagSummary] = []
    because_favorite: FavoriteAttribution | None = None


class RecommendedImagesResponse(BaseModel):
    """Personalized feed envelope (standard list shape + profile_ready flag)."""

    total: int
    page: int
    per_page: int
    profile_ready: bool
    images: list[RecommendedImageResponse]
