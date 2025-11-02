"""
Application Configuration - MySQL Version
Uses Pydantic Settings for environment-based configuration
"""
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra env vars like MYSQL_* used by docker-compose
    )
    
    # Application
    PROJECT_NAME: str = "Shuushuu API"
    VERSION: str = "2.0.0"
    ENVIRONMENT: str = Field(default="development", pattern="^(development|staging|production)$")
    DEBUG: bool = Field(default=False)
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str = Field(
        default="CHANGE-ME-IN-.ENV-FILE-OR-DOCKER-COMPOSE-32-CHARS-LONG",
        min_length=32,
        description="Secret key for JWT tokens - MUST be changed in production"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS
    # Allow str because it can be a comma-separated string in .env
    CORS_ORIGINS: str | List[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"]
    )
    ALLOWED_HOSTS: str | List[str] = Field(default=["*"])
    
    # MySQL Database - UPDATED!
    DATABASE_URL: str = Field(
        default="mysql+aiomysql://shuushuu:password@localhost:3306/shuushuu?charset=utf8mb4"
    )
    # Sync URL for Alembic migrations
    DATABASE_URL_SYNC: str = Field(
        default="mysql+pymysql://shuushuu:password@localhost:3306/shuushuu?charset=utf8mb4"
    )
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_ECHO: bool = False
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    CACHE_TTL: int = 300  # 5 minutes
    
    # Task Queue - UPDATED to support multiple options
    TASK_QUEUE_TYPE: str = Field(
        default="background",  # Options: "background", "arq", "celery"
        pattern="^(background|arq|celery)$"
    )
    
    # Arq settings (if using Arq)
    ARQ_REDIS_URL: str = Field(default="redis://localhost:6379/1")
    ARQ_MAX_TRIES: int = 3
    ARQ_KEEP_RESULT: int = 3600  # 1 hour
    
    # Celery settings (if using Celery - probably won't need)
    CELERY_BROKER_URL: Optional[str] = Field(default=None)
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None)
    
    # File Storage
    STORAGE_TYPE: str = Field(default="local", pattern="^(local|s3)$")
    STORAGE_PATH: str = "/shuushuu/images"
    
    # S3 Configuration (if using S3)
    S3_BUCKET: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_ENDPOINT: Optional[str] = None
    S3_REGION: str = "us-east-1"
    
    # Image Processing
    MAX_IMAGE_SIZE: int = 16 * 1024 * 1024  # 16MB
    MAX_THUMB_WIDTH: int = 250
    MAX_THUMB_HEIGHT: int = 200
    MEDIUM_EDGE: int = 1280
    LARGE_EDGE: int = 2048
    THUMBNAIL_QUALITY: int = 80
    LARGE_QUALITY: int = 90
    
    # IQDB (Image Query Database)
    IQDB_HOST: str = "localhost"
    IQDB_PORT: int = 5588
    IQDB_SIMILARITY_THRESHOLD: float = 50.0
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    UPLOAD_DELAY_SECONDS: int = 30
    SEARCH_DELAY_SECONDS: int = 2
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 15
    MAX_PAGE_SIZE: int = 100
    
    # Email (for notifications)
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: str = "noreply@e-shuushuu.net"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Frontend URL (for email links, etc.)
    FRONTEND_URL: str = "http://localhost:3000"
    
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from comma-separated string"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v
    
    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v):
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
    INAPPROPRIATE = -2
    REPOST = -1
    OTHER = 0
    ACTIVE = 1
    SPOILER = 2


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
    OTHER = 127
    
    LABELS = {
        REPOST: "Repost",
        INAPPROPRIATE: "Inappropriate Image",
        SPAM: "Spam",
        MISSING_TAGS: "Missing Tag Info",
        OTHER: "Other",
    }


class PermissionLevel:
    """User permission levels"""
    ANONYMOUS = 0
    USER = 1
    TAGGER = 2
    MODERATOR = 3
    ADMIN = 4