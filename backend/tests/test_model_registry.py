from app.services.model_registry import merge_models, registration_view


def row(model_id, groups=None):
    return {
        "model_name": "shared",
        "litellm_params": {"model": "openai/upstream"},
        "model_info": {"id": model_id, "db_model": True, "access_groups": groups or []},
    }


def test_shared_alias_keeps_every_connection_and_one_catalog():
    result = merge_models(
        [row("a", ["a-team"]), row("b", ["b-team"])], {"shared": {"visible": True}}, admin=False, sanitize=lambda r: r
    )
    assert len(result) == 1
    assert result[0]["catalog"] == {"visible": True}
    assert [r["model_info"]["id"] for r in result[0]["registrations"]] == ["a", "b"]
    assert result[0]["litellm_info"]["model_info"]["access_groups"] == ["a-team", "b-team"]


def test_hidden_catalog_never_reappears_through_another_registration():
    assert merge_models([row("a"), row("b")], {"shared": {"visible": False}}, admin=False, sanitize=lambda r: r) == []
    assert len(merge_models([row("a")], {}, admin=True, sanitize=lambda r: r)) == 1


def test_registration_projection_does_not_return_credentials():
    r = row("a")
    r["litellm_params"].update(
        api_key="private",
        api_base="https://name:password@api.example/v1?token=private",
        extra_headers={"Authorization": "private"},
        aws_secret_access_key="private",
    )
    projected = registration_view(r)
    assert projected["api_base"] == "https://api.example/v1"
    assert "private" not in str(projected) and "password" not in str(projected)
    assert projected["editable"]
    assert not registration_view(r, {"a": {"id": "dep"}})["editable"]
    r["model_info"]["db_model"] = False
    assert not registration_view(r)["editable"]


def test_external_api_classification_matches_routing_graph():
    from app.services.serving_topology import _cloud_provider, _endpoint
    r = row("external")
    r["litellm_params"]["api_base"] = "https://private-api.example/v1"
    assert registration_view(r)["origin"] == "unknown"
    r["model_info"]["portal_connection_type"] = "external"
    assert registration_view(r)["origin"] == "cloud"
    assert _cloud_provider(r["litellm_params"], r["model_info"], _endpoint(r["litellm_params"]["api_base"]))
    assert registration_view(r, {"external": {"id": "dep"}})["origin"] == "cluster"
