"""Configuration for the inference gateway.

Minimal settings — where the LiteLLM proxy lives, how to verify upstream
TLS, and how to reach the management backend for keyless / SSO auth.  Uses
the same ``APP_`` env prefix as the management backend so existing
deployment env vars carry over (``APP_LITELLM_BASE_URL``).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    litellm_base_url: str = "http://localhost:4000"

    # "" / "true" -> verify with system CAs; "false" -> disable;
    # any other value -> path to a CA bundle.
    ssl_verify_setting: str = ""

    # ── Trusted-system (keyless) auth ───────────────────────────
    # Management backend used to resolve a trusted system to its LiteLLM key.
    # Empty backend_base_url disables the keyless path entirely.
    backend_base_url: str = ""
    backend_api_key: str = ""  # X-Api-Key shared with backend's external API
    # Header names (lowercased) the gateway looks for on keyless requests.
    system_id_header: str = "x-system-id"
    system_secret_header: str = "x-system-secret"
    emp_no_header: str = "emp-no"
    # Seconds to cache a successful system->key resolution.
    system_auth_cache_ttl: int = 60

    # ── Keycloak SSO (user access token -> user's SSO key) ──────
    # Issuer (`iss`) of the Keycloak tokens web apps forward, e.g.
    # https://sso.example.com/realms/litellm. Must equal the backend's
    # keycloak_issuer. Empty disables the SSO path; the backend settings
    # above (backend_base_url / backend_api_key) are required as well.
    keycloak_issuer: str = ""
    # Optional header selecting a team's SSO key instead of the personal one.
    team_header: str = "x-team-id"
    # Upper bound on caching a token->key resolution (never past the token's exp).
    sso_cache_ttl: int = 60
    # Prune the in-memory cache beyond this many entries.
    sso_cache_max_entries: int = 10000

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
