"""
FastAPI Application - Shuushuu API
Modern backend for Shuushuu anime image board
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    print("🚀 Shuushuu API starting up...")
    print(f"📊 Environment: {settings.ENVIRONMENT}")
    print(
        f"🗄️  Database: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else 'configured'}"
    )
    yield
    # Shutdown
    print("👋 Shuushuu API shutting down...")


# Create FastAPI application
app = FastAPI(
    title="Shuushuu API",
    description="Modern FastAPI backend for Shuushuu anime image board",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - API information"""
    return {
        "name": "Shuushuu API",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}


# Import and include routers
from app.api.v1 import router as api_v1_router  # noqa: E402

app.include_router(api_v1_router, prefix="/api/v1")
