"""GET /images keeps its exact query parameters across the filter-builder extraction.

list_images' filter parameters moved into a shared dependency so the search-context
neighbors endpoint can build the identical filtered query
(<shuushuu-frontend-repo>/docs/plans/2026-Q4/2026-11-09-search-context-navigation-impl.md,
Task 1). The frontend's API types are generated from this schema, so a dropped,
renamed or retyped parameter is a client break. The literal below was captured from
main before the move.

Parameters are keyed by (in, name), which is what identifies one in OpenAPI; their
order is not part of the contract. FastAPI lists an endpoint's own query parameters
before its dependencies', so the move put ``include_comments`` first.
"""

import pytest

from app.main import app

EXPECTED_PARAMETERS = {
    ("query", "user_id"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": "Filter by uploader user ID",
            "title": "User Id",
        },
        "description": "Filter by uploader user ID",
    },
    ("query", "favorited_by_user_id"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": "Filter by user who favorited the image",
            "title": "Favorited By User Id",
        },
        "description": "Filter by user who favorited the image",
    },
    ("query", "exclude_user_id"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated user IDs whose uploads to exclude (e.g., '5,6')",
            "title": "Exclude User Id",
        },
        "description": "Comma-separated user IDs whose uploads to exclude (e.g., '5,6')",
    },
    ("query", "exclude_favorited_by_user_id"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated user IDs; exclude images any of them favorited",
            "title": "Exclude Favorited By User Id",
        },
        "description": "Comma-separated user IDs; exclude images any of them favorited",
    },
    ("query", "status"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}],
            "description": "Filter by status (1=active, 2=spoiler, etc). Repeat for multiple.",
            "title": "Status",
        },
        "description": "Filter by status (1=active, 2=spoiler, etc). Repeat for multiple.",
    },
    ("query", "tags"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated tag IDs (e.g., '1,2,3')",
            "title": "Tags",
        },
        "description": "Comma-separated tag IDs (e.g., '1,2,3')",
    },
    ("query", "tags_mode"): {
        "required": False,
        "schema": {
            "type": "string",
            "pattern": "^(any|all)$",
            "description": "Match ANY or ALL tags",
            "default": "any",
            "title": "Tags Mode",
        },
        "description": "Match ANY or ALL tags",
    },
    ("query", "tag_depth"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "maximum": 9, "minimum": 0}, {"type": "null"}],
            "description": "How many levels of child tags to include. 0=exact tag only, 1=tag+direct children, ..., 9=up to 9 levels. Omit for full hierarchy.",
            "title": "Tag Depth",
        },
        "description": "How many levels of child tags to include. 0=exact tag only, 1=tag+direct children, ..., 9=up to 9 levels. Omit for full hierarchy.",
    },
    ("query", "exclude_tags"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated tag IDs to exclude (e.g., '4,5,6')",
            "title": "Exclude Tags",
        },
        "description": "Comma-separated tag IDs to exclude (e.g., '4,5,6')",
    },
    ("query", "exclude_descendants"): {
        "required": False,
        "schema": {
            "type": "boolean",
            "description": "When true, each excluded tag also excludes its child tags (its full hierarchy). Default false (exact-match exclusion).",
            "default": False,
            "title": "Exclude Descendants",
        },
        "description": "When true, each excluded tag also excludes its child tags (its full hierarchy). Default false (exact-match exclusion).",
    },
    ("query", "missing_tag_types"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated tag type IDs the image must be MISSING (1=Theme, 2=Source, 3=Artist, 4=Character).",
            "title": "Missing Tag Types",
        },
        "description": "Comma-separated tag type IDs the image must be MISSING (1=Theme, 2=Source, 3=Artist, 4=Character).",
    },
    ("query", "missing_tag_types_mode"): {
        "required": False,
        "schema": {
            "type": "string",
            "pattern": "^(any|all)$",
            "description": "Match ANY or ALL missing types",
            "default": "any",
            "title": "Missing Tag Types Mode",
        },
        "description": "Match ANY or ALL missing types",
    },
    ("query", "date_from"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Start date (YYYY-MM-DD)",
            "title": "Date From",
        },
        "description": "Start date (YYYY-MM-DD)",
    },
    ("query", "date_to"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "End date (YYYY-MM-DD)",
            "title": "Date To",
        },
        "description": "End date (YYYY-MM-DD)",
    },
    ("query", "min_width"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
            "description": "Minimum width in pixels",
            "title": "Min Width",
        },
        "description": "Minimum width in pixels",
    },
    ("query", "max_width"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
            "description": "Maximum width in pixels",
            "title": "Max Width",
        },
        "description": "Maximum width in pixels",
    },
    ("query", "min_height"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
            "description": "Minimum height in pixels",
            "title": "Min Height",
        },
        "description": "Minimum height in pixels",
    },
    ("query", "max_height"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
            "description": "Maximum height in pixels",
            "title": "Max Height",
        },
        "description": "Maximum height in pixels",
    },
    ("query", "min_rating"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "number", "maximum": 10, "minimum": 1}, {"type": "null"}],
            "description": "Minimum rating (1-10)",
            "title": "Min Rating",
        },
        "description": "Minimum rating (1-10)",
    },
    ("query", "min_favorites"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}],
            "description": "Minimum favorite count",
            "title": "Min Favorites",
        },
        "description": "Minimum favorite count",
    },
    ("query", "min_num_ratings"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}],
            "description": "Minimum number of ratings",
            "title": "Min Num Ratings",
        },
        "description": "Minimum number of ratings",
    },
    ("query", "commenter"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": "Filter by user who commented on the image",
            "title": "Commenter",
        },
        "description": "Filter by user who commented on the image",
    },
    ("query", "exclude_commenter"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Comma-separated user IDs; exclude images any of them commented on",
            "title": "Exclude Commenter",
        },
        "description": "Comma-separated user IDs; exclude images any of them commented on",
    },
    ("query", "commentsearch"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Substring search in comment text",
            "title": "Commentsearch",
        },
        "description": "Substring search in comment text",
    },
    ("query", "commentsearch_mode"): {
        "required": False,
        "schema": {
            "anyOf": [
                {"type": "string", "pattern": "^(all_words|natural|boolean|like)$"},
                {"type": "null"},
            ],
            "description": "Search mode: all_words (default, every term required), like (whole string as one substring); natural and boolean are accepted and behave as all_words",
            "title": "Commentsearch Mode",
        },
        "description": "Search mode: all_words (default, every term required), like (whole string as one substring); natural and boolean are accepted and behave as all_words",
    },
    ("query", "hascomments"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "boolean"}, {"type": "null"}],
            "description": "Filter to images that have comments (true) or no comments (false)",
            "title": "Hascomments",
        },
        "description": "Filter to images that have comments (true) or no comments (false)",
    },
    ("query", "reported"): {
        "required": False,
        "schema": {
            "anyOf": [{"type": "boolean"}, {"type": "null"}],
            "description": "Filter to images with a pending report. Requires report_view; ignored for other viewers.",
            "title": "Reported",
        },
        "description": "Filter to images with a pending report. Requires report_view; ignored for other viewers.",
    },
    ("query", "include_comments"): {
        "required": False,
        "schema": {
            "type": "boolean",
            "description": "Bundle every comment on the returned images into the response, grouped by image id (comments map). Default false.",
            "default": False,
            "title": "Include Comments",
        },
        "description": "Bundle every comment on the returned images into the response, grouped by image id (comments map). Default false.",
    },
    ("query", "page"): {
        "required": False,
        "schema": {"type": "integer", "minimum": 1, "default": 1, "title": "Page"},
        "description": None,
    },
    ("query", "per_page"): {
        "required": False,
        "schema": {
            "type": "integer",
            "maximum": 100,
            "minimum": 1,
            "default": 20,
            "title": "Per Page",
        },
        "description": None,
    },
    ("query", "sort_by"): {
        "required": False,
        "schema": {"$ref": "#/components/schemas/ImageSortBy", "default": "image_id"},
        "description": None,
    },
    ("query", "sort_order"): {
        "required": False,
        "schema": {
            "enum": ["ASC", "DESC"],
            "type": "string",
            "default": "DESC",
            "title": "Sort Order",
        },
        "description": None,
    },
    ("cookie", "access_token"): {
        "required": False,
        "schema": {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Access Token"},
        "description": None,
    },
}


@pytest.mark.api
def test_list_images_query_parameters_unchanged():
    parameters = app.openapi()["paths"]["/api/v1/images"]["get"]["parameters"]
    actual = {
        (p["in"], p["name"]): {
            "required": p["required"],
            "schema": p["schema"],
            "description": p.get("description"),
        }
        for p in parameters
    }
    assert len(actual) == len(parameters), "duplicate (in, name) parameter"
    assert actual == EXPECTED_PARAMETERS
