"""Response schema for the upload-form analyze endpoint."""

from pydantic import BaseModel, Field


class AnalyzedTag(BaseModel):
    """A single resolved internal tag suggestion."""

    tag_id: int
    title: str
    type: int  # internal tag type: theme=1, source=2, artist=3, character=4
    confidence: float  # mapping-scaled model confidence, 0-1 (surfaced for evaluation)
    superseded_by_tag_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Suggested descendants of this tag that are also in this response."
            " Non-empty means the tag is redundant (hierarchy expansion at search"
            " time already covers it) and the form should demote it rather than"
            " offer it alongside its own children."
        ),
    )


class AnalyzeTagsResponse(BaseModel):
    """Theme + character suggestions for an uploaded image, in display order."""

    suggestions: list[AnalyzedTag]
