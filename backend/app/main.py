"""FastAPI application entry point for LiteLLM Portal Backend."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, inference, keys, me, models_catalog, team_requests, teams
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="LiteLLM Portal Backend",
    description="Custom backend for LiteLLM team & model management",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(teams.router)
app.include_router(keys.router)
app.include_router(team_requests.router)
app.include_router(models_catalog.router)
app.include_router(inference.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
