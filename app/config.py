"""
Application Configuration - MariaDB Version
Uses Pydantic Settings for environment-based configuration
"""

from pydantic import Field, field_validator, model_validator
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
    # bcrypt cost factor (gensalt accepts 4-31); tests override to 4 for
    # speed (see tests/conftest.py)
    BCRYPT_ROUNDS: int = Field(default=12, ge=4, le=31)

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

    # Meilisearch
    MEILISEARCH_URL: str = Field(default="http://localhost:7700")
    MEILISEARCH_API_KEY: str | None = Field(default=None)

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

    # Local filesystem root for image storage (used as fallback when R2 is
    # disabled, or as the source for R2 uploads during phase 1).
    STORAGE_PATH: str = "/shuushuu/images"

    # Cloudflare R2 (image storage)
    R2_ENABLED: bool = Field(
        default=False,
        description="Enable R2 image serving. When false, app uses local FS only.",
    )
    R2_ACCESS_KEY_ID: str = Field(default="")
    R2_SECRET_ACCESS_KEY: str = Field(default="")
    R2_ENDPOINT: str = Field(default="", description="R2 S3-compatible endpoint URL")
    R2_PUBLIC_BUCKET: str = Field(default="shuushuu-images")
    R2_PRIVATE_BUCKET: str = Field(default="shuushuu-images-private")
    R2_PUBLIC_CDN_URL: str = Field(
        default="",
        description="Custom domain attached to the public R2 bucket (no trailing slash)",
    )
    R2_PRESIGN_TTL_SECONDS: int = Field(default=900, ge=60, le=3600)
    R2_ALLOW_BULK_BACKFILL: bool = Field(
        default=False,
        description=(
            "Gate for r2_sync.py backfill-locations and reconcile. "
            "Set true permanently in prod; leave false on staging to prevent "
            "mass-uploading prod-imported images to the staging bucket."
        ),
    )

    # Cloudflare API (for CDN cache purge)
    CLOUDFLARE_API_TOKEN: str = Field(default="")
    CLOUDFLARE_ZONE_ID: str = Field(default="")

    # Image Processing
    MAX_IMAGE_SIZE: int = 32 * 1024 * 1024  # 32MB
    MAX_THUMB_WIDTH: int = 500  # Thumbnail longest edge (WebP format)
    MAX_THUMB_HEIGHT: int = 500
    MEDIUM_EDGE: int = 1280
    LARGE_EDGE: int = 2048
    THUMBNAIL_QUALITY: int = 75  # WebP quality (75 is sweet spot for thumbnails)
    LARGE_QUALITY: int = 90

    # ML Tag Suggestions
    ML_TAG_SUGGESTIONS_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for ML tag suggestions. When true the arq worker "
            "loads the ONNX model at startup (and fails to start if model "
            "files are missing), uploads enqueue generation jobs, and the "
            "generate endpoint is available."
        ),
    )
    ML_MODELS_PATH: str = Field(
        default="ml_models",
        description=(
            "Directory holding ONNX model subdirectories; relative paths"
            " resolve against the project root"
        ),
    )
    ML_MODEL_NAME: str = Field(
        default="wd-swinv2-tagger-v3",
        description=(
            "Model subdirectory to load: wd-swinv2-tagger-v3 or an animetimm"
            " name like swinv2_base_window8_256.dbv4-full"
        ),
    )
    ML_MIN_CONFIDENCE: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Minimum model probability for a prediction to become a suggestion",
    )
    ML_PARENT_SUPERSEDE_MIN_CONFIDENCE: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "A suggested child tag supersedes (drops) its suggested parent tags "
            "only when the child's confidence is at least this; a weaker child "
            "leaves the parent in place"
        ),
    )

    # ML suggestions on upload (analyze endpoint)
    ML_ANALYZE_RATE_LIMIT: int = Field(
        default=20, description="Max /analyze calls per user per minute"
    )
    ML_ANALYZE_CONCURRENCY: int = Field(
        default=2, description="Max concurrent inferences process-wide (global semaphore)"
    )
    ML_ANALYZE_SEMAPHORE_TIMEOUT: float = Field(
        default=8.0,
        description="Seconds to wait for an inference slot before returning 429",
    )
    ML_ANALYZE_MIN_CONFIDENCE: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Display floor for upload-form suggestions (separate from ML_MIN_CONFIDENCE used for stored suggestions)",
    )
    ML_ANALYZE_MAX_SUGGESTIONS: int = Field(
        default=12, description="Max suggestions returned per tag type from /analyze"
    )
    ML_ANALYZE_DOWNSCALE_EDGE: int = Field(
        default=2048,
        description=(
            "Longest edge images are downscaled to before inference (the model "
            "resizes to ~448 internally); larger images are scaled down, not rejected"
        ),
    )
    ML_INTRA_OP_THREADS: int = Field(
        default=0,
        description="onnxruntime intra-op thread cap per inference; 0 = library default (all cores)",
    )
    ML_ANALYZE_CACHE_TTL_SECONDS: int = Field(
        default=3600, description="TTL for the md5 -> raw-predictions analyze cache"
    )

    # Avatar Settings
    AVATAR_STORAGE_PATH: str = ""  # Derived from STORAGE_PATH if not set
    BANNER_STORAGE_PATH: str = ""  # Derived from STORAGE_PATH if not set
    MAX_AVATAR_SIZE: int = 1 * 1024 * 1024  # 1MB max upload size
    MAX_AVATAR_DIMENSION: int = 200  # Max width/height after resize

    # IQDB (Image Query Database)
    IQDB_HOST: str = "localhost"
    IQDB_PORT: int = 5588
    IQDB_SIMILARITY_THRESHOLD: float = 50.0
    IQDB_UPLOAD_THRESHOLD: float = 90.0

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    UPLOAD_DELAY_SECONDS: int = 30
    SEARCH_DELAY_SECONDS: int = 2
    MAX_SEARCH_TAGS: int = 5
    SIMILARITY_CHECK_RATE_LIMIT: int = 5  # Max similarity checks per user per minute
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
    SMTP_USER: str = Field(default="", description="SMTP username (empty for local relay)")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password (empty for local relay)")
    SMTP_TLS: bool = Field(default=False, description="Use implicit TLS (port 465)")
    SMTP_STARTTLS: bool = Field(default=True, description="Use STARTTLS (port 587)")
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
    REVIEW_EARLY_CLOSE_MARGIN: int = Field(
        default=3, ge=1
    )  # Vote margin to auto-close before deadline

    # Frontend URL (for email links, verification links, etc.)
    # Development: http://localhost:5173 (Vite dev server) or http://localhost:3000 (via nginx)
    # Production: https://e-shuushuu.net (your domain)
    FRONTEND_URL: str = "http://localhost:5173"

    # Image Base URL (where images are served from - must be your public domain)
    # Development: http://localhost:3000 (via nginx) or http://localhost:8000 (direct FastAPI)
    # Production: https://e-shuushuu.net (HTTPS required for internet-exposed domain)
    # CRITICAL: Must match the URL users see in their browser, or image URLs will be broken
    IMAGE_BASE_URL: str = "http://localhost:3000"

    # Banner Settings
    # If not set, defaults to f"{IMAGE_BASE_URL}/images/banners"
    BANNER_BASE_URL: str = Field(default="")
    # Cache durations for rotating banners (seconds)
    BANNER_CACHE_TTL: int = Field(default=600, ge=0)
    BANNER_CACHE_TTL_JITTER: int = Field(default=300, ge=0)

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

    @model_validator(mode="after")
    def validate_smtp_tls_settings(self) -> Settings:
        """Validate that SMTP_TLS and SMTP_STARTTLS are not both enabled."""
        if self.SMTP_TLS and self.SMTP_STARTTLS:
            raise ValueError(
                "SMTP_TLS and SMTP_STARTTLS are mutually exclusive. "
                "Use SMTP_TLS=true for implicit TLS (port 465), "
                "or SMTP_STARTTLS=true for STARTTLS (port 587), "
                "or both false for unencrypted localhost relay."
            )
        return self

    @model_validator(mode="after")
    def validate_r2_enabled_requirements(self) -> Settings:
        """When R2_ENABLED=true, R2 credentials must be set.

        Cloudflare credentials are optional — purge_cache_by_urls raises at
        call time if they're missing, so R2 works without CDN purging.
        """
        if not self.R2_ENABLED:
            return self
        required = {
            "R2_ACCESS_KEY_ID": self.R2_ACCESS_KEY_ID,
            "R2_SECRET_ACCESS_KEY": self.R2_SECRET_ACCESS_KEY,
            "R2_ENDPOINT": self.R2_ENDPOINT,
            "R2_PUBLIC_BUCKET": self.R2_PUBLIC_BUCKET,
            "R2_PRIVATE_BUCKET": self.R2_PRIVATE_BUCKET,
            "R2_PUBLIC_CDN_URL": self.R2_PUBLIC_CDN_URL,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"R2_ENABLED=true but these required settings are empty: {', '.join(missing)}"
            )
        return self

    @model_validator(mode="after")
    def set_default_banner_base_url(self) -> Settings:
        if not self.BANNER_BASE_URL:
            self.BANNER_BASE_URL = f"{self.IMAGE_BASE_URL}/images/banners"
        return self

    @model_validator(mode="after")
    def set_default_avatar_storage_path(self) -> Settings:
        if not self.AVATAR_STORAGE_PATH:
            self.AVATAR_STORAGE_PATH = f"{self.STORAGE_PATH}/avatars"
        return self

    @model_validator(mode="after")
    def set_default_banner_storage_path(self) -> Settings:
        if not self.BANNER_STORAGE_PATH:
            self.BANNER_STORAGE_PATH = f"{self.STORAGE_PATH}/banners"
        return self


# Create global settings instance

settings = Settings()


# Constants (from original PHP config)
class ImageStatus:
    """Image status constants"""

    REVIEW = -4
    LOW_QUALITY = -3  # legacy: no longer settable; kept for historical rows
    INAPPROPRIATE = -2  # legacy: no longer settable; kept for historical rows
    REPOST = -1
    DEACTIVATED = 0  # reuses the historical generic "disable" bucket (was OTHER)
    OTHER = 0  # DEPRECATED alias of DEACTIVATED; remove once test refs migrate
    ACTIVE = 1
    SPOILER = 2

    # Status values where we show the user who made the change in public audit
    # Show for: REPOST (-1), SPOILER (2), ACTIVE (1)
    # Hide for: REVIEW (-4), LOW_QUALITY (-3), INAPPROPRIATE (-2), DEACTIVATED (0)
    VISIBLE_USER_STATUSES: set[int] = {REPOST, SPOILER, ACTIVE}

    LABELS: dict[int, str] = {
        REVIEW: "review",
        LOW_QUALITY: "low_quality",  # legacy label for historical rows
        INAPPROPRIATE: "inappropriate",  # legacy label for historical rows
        REPOST: "repost",
        DEACTIVATED: "deactivated",  # key 0 — replaces the old "other" label
        ACTIVE: "active",
        SPOILER: "spoiler",
    }

    @classmethod
    def get_label(cls, status: int) -> str:
        """Get human-readable label for image status."""
        return cls.LABELS.get(status, "unknown")


class DeactivationReason:
    """Reason categories for a DEACTIVATED image. Shown publicly."""

    INAPPROPRIATE = 1
    LOW_QUALITY = 2
    SPAM = 3
    OTHER = 4

    LABELS: dict[int, str] = {
        INAPPROPRIATE: "Inappropriate",
        LOW_QUALITY: "Low Quality",
        SPAM: "Spam",
        OTHER: "Other",
    }

    VALID: set[int] = {INAPPROPRIATE, LOW_QUALITY, SPAM, OTHER}

    @classmethod
    def get_label(cls, value: int | None) -> str:
        if value is None:
            return ""
        return cls.LABELS.get(value, "unknown")


class ReportStatus:
    """Report status constants"""

    PENDING = 0
    REVIEWED = 1
    DISMISSED = 2

    LABELS = {
        PENDING: "Pending",
        REVIEWED: "Reviewed",
        DISMISSED: "Dismissed",
    }


class ReviewStatus:
    """Review session status constants"""

    OPEN = 0
    CLOSED = 1


class ReviewOutcome:
    """Review outcome constants"""

    PENDING = 0
    KEEP = 1
    REMOVE = 2


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
    IMAGE_DELETE = 9


class TagAuditActionType:
    """Action types for tag audit log."""

    RENAME = "rename"
    TYPE_CHANGE = "type_change"
    DESCRIPTION_CHANGE = "description_change"
    ALIAS_SET = "alias_set"
    ALIAS_REMOVED = "alias_removed"
    PARENT_SET = "parent_set"
    PARENT_REMOVED = "parent_removed"
    SOURCE_LINKED = "source_linked"
    SOURCE_UNLINKED = "source_unlinked"


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
    TAG_SUGGESTIONS = 4  # Renamed from MISSING_TAGS
    SPOILER = 5
    OTHER = 127

    LABELS = {
        REPOST: "Repost",
        INAPPROPRIATE: "Inappropriate Image",
        SPAM: "Spam",
        TAG_SUGGESTIONS: "Tag Suggestions",  # Updated label
        SPOILER: "Spoiler",
        OTHER: "Other",
    }


class CommentReportCategory:
    """Comment report category constants"""

    RULE_VIOLATION = 1
    SPAM = 2
    OTHER = 127

    LABELS = {
        RULE_VIOLATION: "Rule Violation",
        SPAM: "Spam",
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
    WARNING = "warning"
