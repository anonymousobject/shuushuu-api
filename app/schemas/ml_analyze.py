"""Response schema for the upload-form analyze endpoint."""

from pydantic import BaseModel


class AnalyzedTag(BaseModel):
    """A single resolved internal tag suggestion."""

    tag_id: int
    title: str
    type: int  # internal tag type: theme=1, source=2, artist=3, character=4
    confidence: float  # mapping-scaled model confidence, 0-1 (surfaced for evaluation)


class AnalyzeTagsResponse(BaseModel):
    """Theme + character suggestions for an uploaded image, in display order."""

    suggestions: list[AnalyzedTag]
