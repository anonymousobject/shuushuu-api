"""
FastAPI Application - Shuushuu API
Modern backend for Shuushuu anime image board
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

# Create FastAPI application
app = FastAPI(
    title="Shuushuu API",
    description="Modern FastAPI backend for Shuushuu anime image board",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
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


@app.on_event("startup")
async def startup_event() -> None:
    """Run on application startup"""
    print("🚀 Shuushuu API starting up...")
    print(f"📊 Environment: {settings.ENVIRONMENT}")
    print(
        f"🗄️  Database: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else 'configured'}"
    )


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Run on application shutdown"""
    print("👋 Shuushuu API shutting down...")
