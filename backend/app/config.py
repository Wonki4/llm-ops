from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm"
    litellm_database_url: str = ""

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
    admin_groups: list[str] = []

    session_secret_key: str = "change-me-in-production-must-be-32-bytes"
    encryption_key: str = ""  # Fernet key for at-rest secrets; derived from session_secret_key if empty
    session_cookie_name: str = "litellm_session"
    session_cookie_secure: bool = False
    session_cookie_domain: str = ""
    session_cookie_samesite: str = "lax"
    session_max_age: int = 86400 * 14

    # How often the worker re-evaluates time-of-day cost rules. Rules are
    # hour-granular, so this is the max lateness of a price transition; the loop
    # also aligns to the interval boundary so an on-the-hour change lands within
    # seconds of the hour, not up to a full interval late.
    cost_schedule_interval_seconds: int = 60

    frontend_url: str = "http://localhost:3002"

    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""
    redis_cluster: bool = False
    redis_catalog_prefix: str = "GENERATIVE:AI:"

    external_api_key: str = ""

    # Model deployment / Kubernetes
    kubeconfig_path: str = ""  # mounted kubeconfig file path; empty disables K8s features
    k8s_default_namespace: str = "default"

    # Image used to run `vllm bench serve` for performance benchmarks when the
    # target isn't a portal-managed serving deployment (which reuses its own image).
    vllm_bench_image: str = "vllm/vllm-openai:latest"

    # llm-d stack (ArgoCD-deployed). Air-gap: internal chart repo + image registry.
    # Isolation: every Application is scoped to a dedicated AppProject so it can
    # never affect other projects' apps.
    # gateway-api-inference-extension "standalone" chart = the EPP / inference
    # scheduler (prefix-cache-aware router). Air-gap: mirror the chart + EPP image
    # to an internal registry and override these.
    llmd_chart_repo: str = "oci://registry.k8s.io/gateway-api-inference-extension/charts"
    llmd_chart_name: str = "standalone"
    llmd_chart_version: str = "v1.5.0"
    llmd_image_registry: str = "registry.k8s.io"
    argo_project: str = "llm-d"

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
