"""
SQLAlchemy Models - Database schema models.

All models are now using SQLModel with inheritance-based security patterns.
The generated.py file is no longer needed as all models have been migrated.

For modifications:
1. Edit the appropriate model file in app/models/
2. Create an Alembic migration to reflect the changes
3. Use Alembic to manage all schema changes going forward
"""

from app.core.database import Base
from app.models.admin_action import AdminActions

# User-related models
from app.models.ban import Bans
from app.models.character_source_link import CharacterSourceLinks
from app.models.comment import Comments

# Junction/relationship tables
from app.models.favorite import Favorites

# Core entity models
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.image_report import ImageReports
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.image_review import ImageReviews

# Utility models
from app.models.misc import (
    Banners,
    EvaTheme,
    Quicklinks,
    Tips,
)
from app.models.ml_model_version import MLModelVersion

# Content models
from app.models.news import News

# Permission system
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups, UserPerms
from app.models.privmsg import Privmsgs
from app.models.refresh_token import RefreshTokens
from app.models.review_vote import ReviewVotes
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.models.tag_mapping import TagMapping
from app.models.tag_suggestion import TagSuggestion
from app.models.user import Users

__all__ = [
    "Base",
    # Core entity models
    "Users",
    "Images",
    "Tags",
    "Comments",
    # Junction/relationship tables
    "Favorites",
    "TagLinks",
    "TagExternalLinks",
    "CharacterSourceLinks",
    "TagHistory",
    "ImageRatings",
    "ImageReports",
    "ImageReportTagSuggestions",
    "ImageReviews",
    "ReviewVotes",
    "AdminActions",
    # User-related models
    "Bans",
    "RefreshTokens",
    "Privmsgs",
    # Content models
    "News",
    # Permission system
    "Groups",
    "Perms",
    "GroupPerms",
    "UserGroups",
    "UserPerms",
    # Utility models
    "Banners",
    "EvaTheme",
    "Tips",
    "Quicklinks",
    # Tag suggestion system
    "TagSuggestion",
    "TagMapping",
    "MLModelVersion",
]
