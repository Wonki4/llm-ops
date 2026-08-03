"""Configuration for the budget/usage MCP server.

Uses the same ``APP_`` env prefix as the management backend so existing
deployment env vars carry over (``APP_DATABASE_URL``, ``APP_LITELLM_DATABASE_URL``).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Portal DB (custom_* tables — portal settings such as hidden_teams_strict).
    database_url: str = "postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal"
    # LiteLLM DB (LiteLLM_* tables). Empty -> fall back to database_url.
    litellm_database_url: str = ""
    # Signing secret for the sk-<jwt> virtual keys. MUST match the backend's
    # keys.py `_KEY_JWT_SECRET`; override in prod via APP_KEY_JWT_SECRET.
    key_jwt_secret: str = "litellm-portal-key-sign"
    debug: bool = False

    model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
