"""Unit tests for the ArgoCD connection registry + REST client + llm-d wiring."""

from app.db.models.custom_argocd_connection import CustomArgocdConnection


def test_argocd_connection_columns():
    cols = set(CustomArgocdConnection.__table__.columns.keys())
    assert {
        "id", "name", "server_url", "token_encrypted", "insecure_skip_verify",
        "is_default", "description", "created_by", "updated_by",
        "created_at", "updated_at",
    } <= cols
    # The plaintext token must never be a column.
    assert "token" not in cols
