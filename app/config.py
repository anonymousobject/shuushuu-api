"""
Application Configuration - MariaDB Version
Uses Pydantic Settings for environment-based configuration
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra env vars like MARIADB_* used by docker-compose
    )

    # Application
    PROJECT_NAME: str = "Shuushuu API"
    VERSION: str = "2.0.0"
    ENVIRONMENT: str = Field(default="development", pattern="^(development|staging|production)$")
    DEBUG: bool = Field(default=False)
    API_V1_STR: str = "/api/v1"

    # Security
    SECRET_KEY: str = "YOU MUST CHANGE THIS TO A SECURE RANDOM VALUE"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # CORS
    # Allow str because it can be a comma-separated string in .env
    CORS_ORIGINS: str | list[str] = Field(
        default=["http://localhost:5173", "http://localhost:8000"]
    )
    ALLOWED_HOSTS: str | list[str] = Field(default=["*"])

    # MariaDB Database - UPDATED!
    DATABASE_URL: str = "YOU MUST SET A VALID MARIADB DATABASE URL"
    # Sync URL for Alembic migrations
    DATABASE_URL_SYNC: str = "YOU MUST SET A VALID MARIADB SYNC DATABASE URL"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_ECHO: bool = False

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    CACHE_TTL: int = 300  # 5 minutes

    # Task Queue - UPDATED to support multiple options
    TASK_QUEUE_TYPE: str = Field(
        default="background",  # Options: "background", "arq", "celery"
        pattern="^(background|arq|celery)$",
    )

    # Arq settings (if using Arq)
    ARQ_REDIS_URL: str = Field(default="redis://localhost:6379/1")
    ARQ_MAX_TRIES: int = 3
    ARQ_KEEP_RESULT: int = 3600  # 1 hour

    # Celery settings (if using Celery - probably won't need)
    CELERY_BROKER_URL: str | None = Field(default=None)
    CELERY_RESULT_BACKEND: str | None = Field(default=None)

    # File Storage
    STORAGE_TYPE: str = Field(default="local", pattern="^(local|s3)$")
    STORAGE_PATH: str = "/shuushuu/images"

    # S3 Configuration (if using S3)
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_ENDPOINT: str | None = None
    S3_REGION: str = "us-east-1"

    # Image Processing
    MAX_IMAGE_SIZE: int = 32 * 1024 * 1024  # 32MB
    MAX_THUMB_WIDTH: int = 500  # Thumbnail longest edge (WebP format)
    MAX_THUMB_HEIGHT: int = 500
    MEDIUM_EDGE: int = 1280
    LARGE_EDGE: int = 2048
    THUMBNAIL_QUALITY: int = 75  # WebP quality (75 is sweet spot for thumbnails)
    LARGE_QUALITY: int = 90

    # Avatar Settings
    AVATAR_STORAGE_PATH: str = "/shuushuu/avatars"
    MAX_AVATAR_SIZE: int = 1 * 1024 * 1024  # 1MB max upload size
    MAX_AVATAR_DIMENSION: int = 200  # Max width/height after resize

    # IQDB (Image Query Database)
    IQDB_HOST: str = "localhost"
    IQDB_PORT: int = 5588
    IQDB_SIMILARITY_THRESHOLD: float = 50.0

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    UPLOAD_DELAY_SECONDS: int = 30
    SEARCH_DELAY_SECONDS: int = 2
    MAX_SEARCH_TAGS: int = 5
    REGISTRATION_RATE_LIMIT: int = Field(
        default=5, description="Max registrations per IP per window"
    )
    REGISTRATION_RATE_WINDOW_HOURS: int = Field(default=1, description="Rate limit window in hours")

    # Pagination
    DEFAULT_PAGE_SIZE: int = 15
    MAX_PAGE_SIZE: int = 100

    # Email (for notifications)
    SMTP_HOST: str = Field(default="localhost", description="SMTP server hostname")
    SMTP_PORT: int = Field(default=587, description="SMTP server port")
    SMTP_USER: str = Field(default="user", description="SMTP username")
    SMTP_PASSWORD: str = Field(default="password", description="SMTP password")
    SMTP_TLS: bool = Field(default=True, description="Use TLS for SMTP")
    SMTP_FROM_EMAIL: str = Field(default="noreply@e-shuushuu.net", description="From email address")
    SMTP_FROM_NAME: str = Field(default="Shuushuu", description="From name")

    # Cloudflare Turnstile
    TURNSTILE_SITE_KEY: str = Field(
        default="1x00000000000000000000AA", description="Turnstile site key (public)"
    )
    TURNSTILE_SECRET_KEY: str = Field(
        default="1x0000000000000000000000000000000AA", description="Turnstile secret key (private)"
    )

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Review System
    REVIEW_DEADLINE_DAYS: int = 7  # Default deadline for review voting
    REVIEW_EXTENSION_DAYS: int = 3  # Extension period when deadline expires without quorum
    REVIEW_QUORUM: int = 3  # Minimum votes required for a decision

    # Frontend URL (for email links, verification links, etc.)
    # Development: http://localhost:5173 (Vite dev server) or http://localhost:3000 (via nginx)
    # Production: https://e-shuushuu.net (your domain)
    FRONTEND_URL: str = "http://localhost:5173"

    # Image Base URL (where images are served from - must be your public domain)
    # Development: http://localhost:3000 (via nginx) or http://localhost:8000 (direct FastAPI)
    # Production: https://e-shuushuu.net (HTTPS required for internet-exposed domain)
    # CRITICAL: Must match the URL users see in their browser, or image URLs will be broken
    IMAGE_BASE_URL: str = "http://localhost:3000"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse CORS origins from comma-separated string"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: str | list[str]) -> list[str]:
        """Parse allowed hosts from comma-separated string"""
        if isinstance(v, str):
            return [host.strip() for host in v.split(",")]
        return v


# Create global settings instance

settings = Settings()


# Constants (from original PHP config)
class ImageStatus:
    """Image status constants"""

    REVIEW = -4
    LOW_QUALITY = -3
    INAPPROPRIATE = -2
    REPOST = -1
    OTHER = 0
    ACTIVE = 1
    SPOILER = 2


class ReportStatus:
    """Report status constants"""

    PENDING = 0
    REVIEWED = 1
    DISMISSED = 2


class ReviewStatus:
    """Review session status constants"""

    OPEN = 0
    CLOSED = 1


class ReviewOutcome:
    """Review outcome constants"""

    PENDING = 0
    KEEP = 1
    REMOVE = 2


class ReviewType:
    """Review type constants"""

    APPROPRIATENESS = 1


class AdminActionType:
    """Admin action type constants for audit logging"""

    REPORT_DISMISS = 1
    REPORT_ACTION = 2
    REVIEW_START = 3
    REVIEW_VOTE = 4
    REVIEW_CLOSE = 5
    REVIEW_EXTEND = 6
    IMAGE_STATUS_CHANGE = 7
    COMMENT_DELETE = 8


class TagType:
    """Tag type constants"""

    ALL = 0
    THEME = 1
    SOURCE = 2
    ARTIST = 3
    CHARACTER = 4


class ReportCategory:
    """Image report category constants"""

    REPOST = 1
    INAPPROPRIATE = 2
    SPAM = 3
    MISSING_TAGS = 4
    SPOILER = 5
    OTHER = 127

    LABELS = {
        REPOST: "Repost",
        INAPPROPRIATE: "Inappropriate Image",
        SPAM: "Spam",
        MISSING_TAGS: "Missing Tag Info",
        SPOILER: "Spoiler",
        OTHER: "Other",
    }


class PermissionLevel:
    """User permission levels"""

    ANONYMOUS = 0
    USER = 1
    TAGGER = 2
    MODERATOR = 3
    ADMIN = 4


class SuspensionAction:
    """Suspension action type constants"""

    SUSPENDED = "suspended"
    REACTIVATED = "reactivated"
