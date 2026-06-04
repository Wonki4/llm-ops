"""Tests for credential normalisation in the inference gateway proxy."""

from app.proxy import _ensure_sk_credential, _forward_headers, _has_credential


class TestEnsureSkCredential:
    def test_bearer_without_prefix_gets_sk(self):
        assert _ensure_sk_credential("Bearer mykey") == "Bearer sk-mykey"

    def test_bearer_with_prefix_unchanged(self):
        assert _ensure_sk_credential("Bearer sk-mykey") == "Bearer sk-mykey"

    def test_lowercase_bearer_scheme_preserved(self):
        assert _ensure_sk_credential("bearer mykey") == "bearer sk-mykey"

    def test_bare_key_without_prefix_gets_sk(self):
        # Anthropic / Azure / Google send the raw key with no scheme.
        assert _ensure_sk_credential("mykey") == "sk-mykey"

    def test_bare_key_with_prefix_unchanged(self):
        assert _ensure_sk_credential("sk-mykey") == "sk-mykey"

    def test_basic_scheme_left_untouched(self):
        # Langfuse uses Basic auth; prefixing would corrupt the base64.
        assert _ensure_sk_credential("Basic dXNlcjpwYXNz") == "Basic dXNlcjpwYXNz"

    def test_aws_sigv4_left_untouched(self):
        value = "AWS4-HMAC-SHA256 Credential=Bearer sk-123/x, SignedHeaders=h, Signature=s"
        assert _ensure_sk_credential(value) == value

    def test_surrounding_whitespace_trimmed(self):
        assert _ensure_sk_credential("Bearer  mykey ") == "Bearer sk-mykey"


class TestForwardHeaders:
    def test_authorization_normalised(self):
        out = _forward_headers({"authorization": "Bearer mykey"})
        assert out["authorization"] == "Bearer sk-mykey"

    def test_anthropic_x_api_key_normalised(self):
        out = _forward_headers({"x-api-key": "mykey"})
        assert out["x-api-key"] == "sk-mykey"

    def test_azure_api_key_normalised(self):
        out = _forward_headers({"api-key": "mykey"})
        assert out["api-key"] == "sk-mykey"

    def test_google_x_goog_api_key_normalised(self):
        out = _forward_headers({"x-goog-api-key": "mykey"})
        assert out["x-goog-api-key"] == "sk-mykey"

    def test_hop_by_hop_dropped(self):
        out = _forward_headers({"host": "example.com", "connection": "keep-alive"})
        assert "host" not in out
        assert "connection" not in out

    def test_non_credential_headers_preserved(self):
        out = _forward_headers({"content-type": "application/json", "user-agent": "x"})
        assert out["content-type"] == "application/json"
        assert out["user-agent"] == "x"


class TestHasCredential:
    def test_authorization_counts(self):
        assert _has_credential({"authorization": "Bearer x"}) is True

    def test_x_api_key_counts(self):
        assert _has_credential({"x-api-key": "x"}) is True

    def test_empty_credential_does_not_count(self):
        assert _has_credential({"authorization": "   "}) is False

    def test_only_emp_no_is_not_a_credential(self):
        assert _has_credential({"emp-no": "12345", "x-system-id": "payroll"}) is False
