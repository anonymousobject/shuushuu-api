"""
Pydantic schemas for Image endpoints
"""

from datetime import datetime

from pydantic import BaseModel, computed_field

from app.models.image import ImageBase


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


class ImageResponse(ImageBase):
    """
    Schema for image response - what API returns.

    Inherits public fields from ImageBase and adds additional public metadata.
    Does NOT include internal fields like IP, user agent, etc.
    """

    image_id: int
    user_id: int
    date_added: datetime | None = None
    status: int
    locked: int
    posts: int
    favorites: int
    bayesian_rating: float
    num_ratings: int

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Generate image URL"""
        return f"/storage/fullsize/{self.filename}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str:
        """Generate thumbnail URL"""
        return f"/storage/thumbs/{self.filename}"


class ImageListResponse(BaseModel):
    """Schema for paginated image list"""

    total: int
    page: int
    per_page: int
    images: list[ImageResponse]


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
