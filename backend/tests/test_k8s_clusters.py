"""Unit tests for the K8s clusters settings feature.

Covers the pure logic that carries the risk: secret encryption round-trip and
kubeconfig parsing/validation. Full CRUD is exercised against the running app.
"""

import pytest
from fastapi import HTTPException

from app.api.k8s_clusters import _parse_kubeconfig
from app.services import crypto

SAMPLE_KUBECONFIG = """
apiVersion: v1
kind: Config
clusters:
- name: prod-cluster
  cluster:
    server: https://10.0.0.1:6443
    insecure-skip-tls-verify: true
- name: dev-cluster
  cluster:
    server: https://10.0.0.2:6443
contexts:
- name: prod
  context:
    cluster: prod-cluster
    user: admin
- name: dev
  context:
    cluster: dev-cluster
    user: admin
users:
- name: admin
  user:
    token: secret-token
"""


def test_crypto_round_trip():
    plaintext = "kubeconfig: contents\nwith: secrets"
    token = crypto.encrypt(plaintext)
    assert token != plaintext
    assert crypto.decrypt(token) == plaintext


def test_crypto_tokens_are_not_plaintext():
    token = crypto.encrypt("super-secret-token")
    assert "super-secret-token" not in token


def test_parse_kubeconfig_resolves_api_server():
    parsed, api_server = _parse_kubeconfig(SAMPLE_KUBECONFIG, "prod")
    assert api_server == "https://10.0.0.1:6443"
    assert parsed["kind"] == "Config"


def test_parse_kubeconfig_resolves_second_context():
    _parsed, api_server = _parse_kubeconfig(SAMPLE_KUBECONFIG, "dev")
    assert api_server == "https://10.0.0.2:6443"


def test_parse_kubeconfig_missing_context_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig(SAMPLE_KUBECONFIG, "staging")
    assert exc.value.status_code == 400
    assert "staging" in exc.value.detail


def test_parse_kubeconfig_bad_yaml_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig("clusters: [unclosed", "prod")
    assert exc.value.status_code == 400


def test_parse_kubeconfig_not_a_kubeconfig_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig("just: a yaml\nwithout: contexts", "prod")
    assert exc.value.status_code == 400
