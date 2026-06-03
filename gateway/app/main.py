"""FastAPI application entry point for the LLM inference gateway.

A thin reverse proxy in front of the LiteLLM proxy that exposes a single
``/v1/*`` inference endpoint to SDK clients, deployed independently from the
management backend.
"""

from fastapi import FastAPI

from app.proxy import router as proxy_router

app = FastAPI(
    title="LLM Inference Gateway",
    description="Reverse proxy for LiteLLM /v1 inference endpoints",
    version="0.1.0",
)

app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
