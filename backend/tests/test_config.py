from app.config import Settings


def test_ingress_defaults():
    s = Settings()
    assert s.llmd_ingress_class == ""
    assert s.llmd_ingress_domain == "llm-d.local"
    assert s.llmd_ingress_path == "/"


def test_effective_ingress_domain_falls_back_when_blank():
    # Operator sets APP_LLMD_INGRESS_DOMAIN="" (or whitespace) -> treated as unset.
    assert Settings(llmd_ingress_domain="").effective_ingress_domain == "llm-d.local"
    assert Settings(llmd_ingress_domain="   ").effective_ingress_domain == "llm-d.local"


def test_effective_ingress_domain_uses_override():
    assert Settings(llmd_ingress_domain="ai.corp.internal").effective_ingress_domain == "ai.corp.internal"
