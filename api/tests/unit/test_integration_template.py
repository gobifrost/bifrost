from src.services.solutions.integration_template import (
    _SAFE_OAUTH_FIELDS,
    build_integration_template,
)


class _Prov:
    provider_name = "halo"; display_name = "HaloPSA"; oauth_flow_type = "authorization_code"
    authorization_url = "https://auth"; token_url = "https://token"; audience = None
    token_url_defaults = {}; entity_id_source = None; scopes = ["all"]; redirect_uri = None
    client_id = "SECRET-CLIENT"; encrypted_client_secret = b"SECRET"


class _Schema:
    key = "url"; type = "string"; required = True; description = None
    options = None; position = 0


class _Integration:
    name = "HaloPSA"; entity_id_name = "tenant"; default_entity_id = None
    list_entities_data_provider_id = None
    config_schema = [_Schema()]; oauth_provider = _Prov()


def test_template_carries_safe_fields_and_scrubs_secrets():
    t = build_integration_template(_Integration())
    assert t["name"] == "HaloPSA"
    assert t["config_schema"][0]["key"] == "url"
    assert t["oauth"]["authorization_url"] == "https://auth"
    assert t["oauth"]["scopes"] == ["all"]
    # Pin the allowlist contract directly so a stray field addition is caught.
    assert set(t["oauth"].keys()) == set(_SAFE_OAUTH_FIELDS)
    # No secret survives anywhere in the serialized template.
    blob = repr(t)
    assert "SECRET-CLIENT" not in blob and "SECRET" not in blob
    assert "client_id" not in t["oauth"]
    assert "client_secret" not in t["oauth"]


def test_template_no_oauth_when_provider_absent():
    class _NoOauth:
        name = "ApiKeyInteg"; entity_id_name = None; default_entity_id = None
        list_entities_data_provider_id = None; config_schema = []; oauth_provider = None
    t = build_integration_template(_NoOauth())
    assert t["oauth"] is None
    assert t["name"] == "ApiKeyInteg"
