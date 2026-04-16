"""FastAPI application entry point for LLM Ops Backend."""

import logging
import traceback
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, budgets, catalog, external, inference, keys, me, models_catalog, portal_settings, team_requests, teams
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="LLM Ops Backend",
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
app.include_router(budgets.router)
app.include_router(portal_settings.router)
app.include_router(catalog.router)
app.include_router(external.router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch unhandled exceptions, log full traceback as ERROR, return 500."""
    logger.error(
        "Unhandled exception on %s %s\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
