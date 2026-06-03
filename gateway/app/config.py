"""Configuration for the inference gateway.

Minimal settings — the gateway only needs to know where the LiteLLM proxy
lives and how to verify upstream TLS.  Uses the same ``APP_`` env prefix as
the management backend so existing deployment env vars carry over
(``APP_LITELLM_BASE_URL``).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    litellm_base_url: str = "http://localhost:4000"

    # "" / "true" -> verify with system CAs; "false" -> disable;
    # any other value -> path to a CA bundle.
    ssl_verify_setting: str = ""

    model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}

    @property
    def ssl_verify(self) -> str | bool:
        if not self.ssl_verify_setting:
            return True
        if self.ssl_verify_setting.lower() == "true":
            return True
        if self.ssl_verify_setting.lower() == "false":
            return False
        return self.ssl_verify_setting


settings = Settings()
