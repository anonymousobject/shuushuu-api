"""
Pydantic schemas for Image endpoints
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

from app.config import TagType, settings
from app.models.image import ImageBase
from app.schemas.common import UserSummary


class TagSummary(BaseModel):
    """Minimal tag info for embedding"""

    tag_id: int
    tag: str = Field(alias="title")  # Maps from Tags.title
    type_id: int = Field(alias="type")  # Maps from Tags.type

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True, "populate_by_name": True}

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
    """Schema for updating an image - all fields optional"""

    filename: str | None = None
    ext: str | None = None
    original_filename: str | None = None
    md5_hash: str | None = None
    filesize: int | None = None
    width: int | None = None
    height: int | None = None
    caption: str | None = None
    rating: float | None = None

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
    date_added: datetime | None = None
    locked: int
    posts: int
    favorites: int
    bayesian_rating: float
    num_ratings: int
    medium: int
    large: int
    replacement_id: int | None = None  # Original image ID when this is a repost (status=-1)

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Generate image URL (protected path with permission check)"""
        return f"{settings.IMAGE_BASE_URL}/images/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str:
        """Generate thumbnail URL (protected path with permission check)"""
        return f"{settings.IMAGE_BASE_URL}/thumbs/{self.filename}.webp"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def medium_url(self) -> str | None:
        """Generate medium variant URL (1280px edge, protected path) if available"""
        if self.medium:
            return f"{settings.IMAGE_BASE_URL}/medium/{self.filename}.{self.ext}"
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def large_url(self) -> str | None:
        """Generate large variant URL (2048px edge, protected path) if available"""
        if self.large:
            return f"{settings.IMAGE_BASE_URL}/large/{self.filename}.{self.ext}"
        return None


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

    model_config = {"from_attributes": True}

    @classmethod
    def from_db_model(
        cls,
        image: Any,
        is_favorited: bool = False,
        user_rating: int | None = None,
        prev_image_id: int | None = None,
        next_image_id: int | None = None,
    ) -> "ImageDetailedResponse":
        """Create response from database model with relationships"""
        data = ImageResponse.model_validate(image).model_dump()

        # Add user if loaded
        if hasattr(image, "user") and image.user:
            data["user"] = UserSummary.model_validate(image.user)

        # Add tags if loaded through tag_links
        if hasattr(image, "tag_links") and image.tag_links:
            data["tags"] = [TagSummary.model_validate(tag_link.tag) for tag_link in image.tag_links]

        data["is_favorited"] = is_favorited
        data["user_rating"] = user_rating
        data["prev_image_id"] = prev_image_id
        data["next_image_id"] = next_image_id

        return cls(**data)


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
