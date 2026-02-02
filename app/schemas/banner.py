"""Banner API schemas.

Defines request/response models for banner endpoints.
"""

from pydantic import BaseModel, computed_field, model_validator

from app.config import settings
from app.models.misc import BannerSize


class BannerResponse(BaseModel):
    """Response schema for banner data."""

    banner_id: int
    name: str
    author: str | None
    size: BannerSize
    supports_dark: bool
    supports_light: bool

    # Raw image paths from database
    full_image: str | None
    left_image: str | None
    middle_image: str | None
    right_image: str | None

    model_config = {"from_attributes": True}

    def _image_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return f"{settings.BANNER_BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_full(self) -> bool:
        """True if this is a full-width banner, False if three-part."""

        return self.full_image is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_image_url(self) -> str | None:
        return self._image_url(self.full_image)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def left_image_url(self) -> str | None:
        return self._image_url(self.left_image)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def middle_image_url(self) -> str | None:
        return self._image_url(self.middle_image)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def right_image_url(self) -> str | None:
        return self._image_url(self.right_image)

    @model_validator(mode="after")
    def _validate_layout(self) -> "BannerResponse":
        """Ensure banner is either full-image OR three-part (all parts present)."""

        has_full = self.full_image is not None
        parts = [self.left_image, self.middle_image, self.right_image]
        has_any_part = any(part is not None for part in parts)
        has_all_parts = all(part is not None for part in parts)

        if has_full and has_any_part:
            raise ValueError("Banner cannot have both full_image and three-part images")
        if not has_full and not has_any_part:
            raise ValueError("Banner must have either full_image or three-part images")
        if has_any_part and not has_all_parts:
            raise ValueError(
                "Three-part banner must include left_image, middle_image, and right_image"
            )

        return self


class BannerListResponse(BaseModel):
    """Paginated banner list response."""

    items: list[BannerResponse]
    total: int
    page: int
    per_page: int
