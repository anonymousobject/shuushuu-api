"""
Pydantic schemas for Image endpoints
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImageBase(BaseModel):
    """Base schema for Image - shared fields"""
    filename: str | None = None
    ext: str
    original_filename: str | None = None
    md5_hash: str
    filesize: int
    width: int
    height: int
    caption: str = ""
    image_source: str | None = None
    artist: str | None = None
    characters: str | None = None
    rating: float = 0.0


class ImageCreate(ImageBase):
    """Schema for creating a new image"""
    user_id: int


class ImageUpdate(ImageBase):
    """Schema for updating an image - all fields optional"""
    # Override ImageBase fields to make them optional for updates
    ext: str | None = None
    md5_hash: str | None = None
    filesize: int | None = None
    width: int | None = None
    height: int | None = None


class ImageResponse(ImageBase):
    """Schema for image response - what API returns"""
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
    @property
    def url(self) -> str:
        """Generate image URL"""
        return f"/storage/fullsize/{self.filename}"

    @property
    def thumbnail_url(self) -> str:
        """Generate thumbnail URL"""
        return f"/storage/thumbs/{self.filename}"

    model_config = ConfigDict(from_attributes=True)


class ImageListResponse(BaseModel):
    """Schema for paginated image list"""
    total: int
    page: int
    per_page: int
    images: list[ImageResponse]


class ImageSearchParams(BaseModel):
    """Schema for image search parameters"""
    tags: str | None = None
    user_id: int | None = None
    sort_by: str = "image_id"
    sort_order: str = "DESC"
    page: int = 1
    per_page: int = 20
