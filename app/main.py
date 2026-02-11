"""
TradeFlow Africa — FastAPI application entry point.

Configures the app, middleware, and registers all API routers.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import auth, traders, transactions, rates, matching, admin
from app.whatsapp.webhook import router as whatsapp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: initialize connections
    from app.database import engine
    from app.redis_client import redis

    yield

    # Shutdown: close connections
    await engine.dispose()
    await redis.aclose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Cross-border B2B payment platform for the Nigeria-China trade corridor.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(traders.router, prefix="/api/v1/traders", tags=["Traders"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["Transactions"])
app.include_router(rates.router, prefix="/api/v1/rates", tags=["Rates"])
app.include_router(matching.router, prefix="/api/v1/matching", tags=["Matching"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(whatsapp_router, prefix="/api/v1/whatsapp", tags=["WhatsApp"])


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": "0.1.0",
    }
