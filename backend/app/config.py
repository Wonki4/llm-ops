from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm"

    litellm_base_url: str = "http://localhost:4000"
    litellm_admin_api_key: str = "sk-1234"

    keycloak_base_url: str = "http://localhost:8082"
    keycloak_internal_url: str = ""
    keycloak_realm: str = "litellm"
    keycloak_client_id: str = "litellm-portal"
    keycloak_client_secret: str = "change-me-in-keycloak"
    keycloak_redirect_uri: str = "http://localhost:8002/api/auth/callback"
    keycloak_idp_hint: str = ""
    keycloak_ssl_verify: str = ""

    jwt_audience: str = "litellm-portal"
    super_user_role: str = "super_user"

    session_secret_key: str = "change-me-in-production-must-be-32-bytes"
    session_cookie_name: str = "litellm_session"
    session_cookie_secure: bool = False
    session_cookie_domain: str = ""
    session_cookie_samesite: str = "lax"
    session_max_age: int = 86400 * 14

    frontend_url: str = "http://localhost:3002"

    slack_webhook_url: str = ""
    cors_origins: list[str] = ["http://localhost:3002"]
    debug: bool = False

    model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}

    @property
    def keycloak_issuer(self) -> str:
        return f"{self.keycloak_base_url}/realms/{self.keycloak_realm}"

    @property
    def keycloak_internal_base(self) -> str:
        return self.keycloak_internal_url or self.keycloak_base_url

    @property
    def keycloak_internal_issuer(self) -> str:
        return f"{self.keycloak_internal_base}/realms/{self.keycloak_realm}"

    @property
    def effective_jwks_uri(self) -> str:
        return f"{self.keycloak_internal_issuer}/protocol/openid-connect/certs"

    @property
    def ssl_verify(self) -> str | bool:
        if not self.keycloak_ssl_verify:
            return True
        if self.keycloak_ssl_verify.lower() == "true":
            return True
        if self.keycloak_ssl_verify.lower() == "false":
            return False
        return self.keycloak_ssl_verify


settings = Settings()
