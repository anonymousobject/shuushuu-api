"""
Pydantic schemas for Tag endpoints
"""

import re

from pydantic import BaseModel, field_validator, model_validator

from app.models.tag import TagBase
from app.schemas.base import UTCDatetime
from app.schemas.common import UserSummary

# Tag name validation constants
TAG_NAME_MIN_LENGTH = 2
TAG_NAME_MAX_LENGTH = 150

# Regex pattern for allowed characters in tag names:
# - Latin letters (a-z, A-Z)
# - Digits (0-9)
# - CJK Unified Ideographs (U+4E00-9FFF) and Extension A (U+3400-4DBF)
# - Hiragana (U+3040-309F)
# - Katakana (U+30A0-30FF)
# - Hangul Syllables (U+AC00-D7AF) and Jamo (U+1100-11FF)
# - Basic punctuation: space, hyphen, period, apostrophe, colon, parentheses,
#   exclamation, question mark, ampersand, forward slash, comma, underscore
TAG_NAME_PATTERN = re.compile(
    r"^["
    r"a-zA-Z"  # Latin letters
    r"0-9"  # Digits
    r"\u4E00-\u9FFF"  # CJK Unified Ideographs
    r"\u3400-\u4DBF"  # CJK Extension A
    r"\u3040-\u309F"  # Hiragana
    r"\u30A0-\u30FF"  # Katakana
    r"\uAC00-\uD7AF"  # Hangul Syllables
    r"\u1100-\u11FF"  # Hangul Jamo
    r" \-\.\'\:\(\)\!\?\&\/\,\_"  # Allowed punctuation
    r"]+$"
)


def validate_tag_name(title: str) -> str:
    """
    Validate and normalize a tag name.

    Rules:
    - Minimum length: 2 characters
    - Maximum length: 150 characters
    - Consecutive spaces normalized to single space
    - Only allowed characters (Latin, digits, CJK, kana, hangul, basic punctuation)

    Args:
        title: The tag name to validate

    Returns:
        The normalized tag name

    Raises:
        ValueError: If the tag name is invalid
    """
    # Trim whitespace first
    title = title.strip()

    # Normalize consecutive spaces to single space
    title = re.sub(r" +", " ", title)

    # Check minimum length
    if len(title) < TAG_NAME_MIN_LENGTH:
        raise ValueError(f"Tag name must be at least {TAG_NAME_MIN_LENGTH} characters")

    # Check maximum length
    if len(title) > TAG_NAME_MAX_LENGTH:
        raise ValueError(f"Tag name cannot exceed {TAG_NAME_MAX_LENGTH} characters")

    # Check allowed characters
    if not TAG_NAME_PATTERN.match(title):
        raise ValueError(
            "Tag name contains invalid characters. "
            "Only letters, numbers, CJK characters, and basic punctuation are allowed."
        )

    return title


class TagCreate(TagBase):
    """Schema for creating a new tag"""

    # Override title to make it required (TagBase has default=None)
    title: str

    inheritedfrom_id: int | None = None
    alias_of: int | None = None
    desc: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """Validate and normalize tag title."""
        return validate_tag_name(v)

    @field_validator("desc")
    @classmethod
    def sanitize_desc(cls, v: str | None) -> str | None:
        """
        Sanitize description.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class TagUpdate(BaseModel):
    """Schema for updating a tag - all fields optional"""

    title: str | None = None
    type: int | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        """Validate and normalize tag title."""
        if v is None:
            return v
        return validate_tag_name(v)


class TagResponse(TagBase):
    """Schema for tag response - what API returns"""

    tag_id: int
    alias_of: int | None = None
    alias_of_name: str | None = None
    is_alias: bool = False
    usage_count: int = 0

    # NOTE: No normalization/escaping for title and desc.
    # These fields are stored as plain text (trimmed on input) and HTML escaping
    # is handled by Svelte's safe template interpolation on the frontend.
    # Legacy data: Run scripts/normalize_db_text.py to decode HTML entities.

    @model_validator(mode="after")
    def set_is_alias(self) -> "TagResponse":
        if self.alias_of is not None:
            self.is_alias = True
        return self


class LinkedTag(BaseModel):
    """Minimal tag info for linked sources/characters and history entries"""

    tag_id: int
    title: str | None
    type: int
    usage_count: int | None = None  # Nullable: None means usage count not loaded


class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    total_image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None  # The actual tag this aliases (if is_alias=True)
    aliases: list[LinkedTag] = []  # Tags that are aliases of this tag
    parent_tag_id: int | None = None  # The parent tag in hierarchy (inheritedfrom_id)
    child_count: int = 0  # Number of child tags that inherit from this tag
    created_by: UserSummary | None = None  # User who created the tag
    date_added: UTCDatetime  # When the tag was created
    links: list["TagExternalLinkResponse"] = []  # External links associated with this tag
    # Character-source links
    sources: list[LinkedTag] = []  # For character tags: linked sources
    characters: list[LinkedTag] = []  # For source tags: linked characters


class TagListResponse(BaseModel):
    """Schema for paginated tag list"""

    total: int
    page: int
    per_page: int
    tags: list[TagResponse]
    invalid_ids: list[str] | None = None  # IDs that were invalid and filtered out


class TagExternalLinkCreate(BaseModel):
    """Schema for adding a new external link to a tag"""

    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL has http/https protocol and trim whitespace."""
        v = v.strip()
        if not v:
            raise ValueError("URL cannot be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2000:
            raise ValueError("URL exceeds maximum length of 2000 characters")
        return v


class TagExternalLinkResponse(BaseModel):
    """Schema for tag external link response"""

    link_id: int
    url: str
    date_added: UTCDatetime

    model_config = {"from_attributes": True}


class CharacterSourceLinkCreate(BaseModel):
    """Schema for creating a character-source link"""

    character_tag_id: int
    source_tag_id: int


class CharacterSourceLinkResponse(BaseModel):
    """Schema for character-source link response"""

    id: int
    character_tag_id: int
    source_tag_id: int
    created_at: UTCDatetime
    created_by_user_id: int | None = None

    model_config = {"from_attributes": True}


class CharacterSourceLinkListResponse(BaseModel):
    """Schema for paginated character-source link list"""

    total: int
    page: int
    per_page: int
    links: list[CharacterSourceLinkResponse]


class CharacterSourceLinkWithTitles(CharacterSourceLinkResponse):
    """Link response with tag titles included"""

    character_title: str | None = None
    source_title: str | None = None
