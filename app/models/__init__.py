"""
SQLAlchemy Models - Auto-generated from existing database schema.

These models were generated using sqlacodegen from the existing MySQL database.
They represent the current state of the database schema.

For modifications:
1. Make changes to the database schema first
2. Regenerate models: uv run sqlacodegen mysql+pymysql://... --outfile app/models/generated.py
3. Create an Alembic migration to track the change
"""

from app.models.generated import (
    Base,
    # Main models
    Users,
    Images,
    Tags,
    Posts,
    # Junction tables
    TagLinks,
    Favorites,
    ImageRatings,
    ImageReports,
    ImageReviews,
    # Supporting models
    Groups,
    GroupPerms,
    UserGroups,
    UserPerms,
    UserSessions,
    Bans,
    Banners,
    News,
    Quicklinks,
    Tips,
    # Tag-related
    TwTags,
    TwTaglink,
    TwTagcluster,
    TwClosest,
    TagHistory,
    # Other
    Privmsgs,
    EvaTheme,
)

__all__ = [
    "Base",
    # Main models
    "Users",
    "Images",
    "Tags",
    "Posts",
    # Junction tables
    "TagLinks",
    "Favorites",
    "ImageRatings",
    "ImageReports",
    "ImageReviews",
    # Supporting models
    "Groups",
    "GroupPerms",
    "UserGroups",
    "UserPerms",
    "UserSessions",
    "Bans",
    "Banners",
    "News",
    "Quicklinks",
    "Tips",
    # Tag-related
    "TwTags",
    "TwTaglink",
    "TwTagcluster",
    "TwClosest",
    "TagHistory",
    # Other
    "Privmsgs",
    "EvaTheme",
]
