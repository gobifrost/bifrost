"""SDK candidates come from real SDK transports and canonical access entries."""

import ast
from uuid import uuid4

import pytest

from shared.workflow_conversion_sdk import (
    permissions_for_recorded_operations,
    permissions_for_sdk_calls,
    recorded_route_candidates,
    sdk_named_permission_candidates,
    sdk_call_contract,
    sdk_object_contract,
    sdk_object_is_data,
    sdk_permission_fingerprint,
    sdk_route_candidates,
)


def test_sdk_method_names_map_to_actual_transport_permissions() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.organizations.create", "bifrost.organizations.list", "bifrost.ai.complete",
    ])
    assert permissions == ["ai.execute", "organizations.read", "organizations.readwrite"]
    assert unknowns == []


def test_file_and_document_policy_candidates_do_not_invent_named_grants() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.files.read", "bifrost.tables.query"])
    assert permissions == []
    assert unknowns == []
    assert [candidate.access_class for candidate in sdk_route_candidates([
        "bifrost.files.read", "bifrost.tables.query",
    ])] == ["table_policy", "table_policy"]


@pytest.mark.parametrize("call,selectors,dynamic,expected", [
    ("bifrost.files.read", {"location": "workspace"}, [], ["repository.read"]),
    ("bifrost.files.write", {"location": "workspace"}, [], ["repository.readwrite"]),
    ("bifrost.files.read", {"location": "uploads"}, [], []),
    ("bifrost.files.read", {}, ["location"], ["repository.read"]),
    ("bifrost.files.get_signed_url", {"location": "workspace", "method": "GET"}, [], ["repository.read"]),
    ("bifrost.files.get_signed_url", {"location": "workspace", "method": "PUT"}, [], ["repository.readwrite"]),
    ("bifrost.files.get_signed_url", {"location": "workspace"}, ["method"], ["repository.read", "repository.readwrite"]),
    ("bifrost.tables.query", {"table": "Contoso Orders"}, [], []),
])
def test_bounded_sdk_arguments_select_named_checks_without_an_identity(
    call, selectors, dynamic, expected
):
    permissions, unknowns = permissions_for_sdk_calls([call], invocations=[{
        "call": call, "selectors": selectors, "dynamic_selectors": dynamic,
    }])
    assert permissions == expected
    assert unknowns == []


@pytest.mark.parametrize("call,path,expected", [
    ("bifrost.api.get", "/api/users", ["users.read"]),
    ("bifrost.api.get", "/api/users/{}", ["users.read"]),
    ("bifrost.api.post", "/api/organizations", ["organizations.readwrite"]),
    ("bifrost.api.get", "/api/files/editor/content?path=contoso.txt", ["repository.read"]),
    ("bifrost.api.post", "/api/sdk/config/get", ["configs.read", "secrets.read"]),
])
def test_generic_sdk_http_calls_use_bounded_canonical_route_templates(call, path, expected):
    permissions, unknowns = permissions_for_sdk_calls([call], invocations=[{
        "call": call, "selectors": {"path": path}, "dynamic_selectors": [],
    }])
    assert permissions == expected
    assert unknowns == []


def test_one_resolved_generic_sdk_call_does_not_hide_an_unbounded_call():
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.api.get"], invocations=[
        {"call": "bifrost.api.get", "selectors": {"path": "/api/config"}},
        {"call": "bifrost.api.get", "selectors": {}, "dynamic_selectors": ["path"]},
    ])
    assert permissions == ["configs.read"]
    assert unknowns == ["No static platform transport mapping for SDK call bifrost.api.get"]


@pytest.mark.parametrize("selectors,dynamic,expected", [
    ({"path": "/api/files/read"}, [], ["repository.read"]),
    ({"path": "/api/files/write"}, [], ["repository.readwrite"]),
    ({"path": "/api/files/read", "location": "uploads"}, [], []),
    ({"path": "/api/files/read"}, ["location"], ["repository.read"]),
    ({"path": "/api/files/signed-url"}, [], []),
    ({"path": "/api/files/signed-url", "location": "workspace", "method": "GET"}, [], ["repository.read"]),
])
def test_generic_sdk_file_routes_retain_workspace_named_checks(selectors, dynamic, expected):
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.api.post"], invocations=[{
        "call": "bifrost.api.post", "selectors": selectors, "dynamic_selectors": dynamic,
    }])
    assert permissions == expected
    assert unknowns == []


@pytest.mark.parametrize("path", ["/api/contoso-unmapped", "/api/{}/unmapped", "https://contoso.invalid/api/users"])
def test_generic_sdk_path_without_a_canonical_route_stays_unresolved(path):
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.api.get"], invocations=[{
        "call": "bifrost.api.get", "selectors": {"path": path},
    }])
    assert permissions == []
    assert unknowns == ["No static platform transport mapping for SDK call bifrost.api.get"]


def test_secret_and_personal_route_candidates_remain_available_without_review() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.config.get", "bifrost.artifacts.list"])
    assert "configs.read" in permissions
    assert "secrets.read" in permissions
    assert not any(permission.endswith(".all") for permission in permissions)
    assert unknowns == []
    candidates = sdk_route_candidates(["bifrost.config.get", "bifrost.artifacts.list"])
    assert [candidate.permission for candidate in candidates] == [
        "artifacts.read.all",
        "secrets.read",
    ]


def test_service_guards_supply_unconditional_and_conditional_sdk_candidates() -> None:
    candidates = sdk_named_permission_candidates([
        "bifrost.config.get",
        "bifrost.integrations.get_mapping",
    ])

    assert [(candidate.call, candidate.permission, candidate.condition) for candidate in candidates] == [
        ("bifrost.config.get", "configs.read", "unconditional"),
        ("bifrost.config.get", "secrets.read", "conditional"),
        ("bifrost.integrations.get_mapping", "integrations.read", "unconditional"),
        ("bifrost.integrations.get_mapping", "secrets.read", "conditional"),
    ]
    permissions, unknowns = permissions_for_recorded_operations(["POST /api/sdk/config/get"])
    assert permissions == ["configs.read", "secrets.read"]
    assert unknowns == []


def test_execution_visibility_guard_is_a_conditional_candidate() -> None:
    candidates = sdk_named_permission_candidates(["bifrost.executions.list"])

    assert [(candidate.permission, candidate.condition) for candidate in candidates] == [
        ("executions.read.all", "conditional"),
    ]


def test_recorded_bulk_user_operation_resolves_its_finite_permission_selection():
    permissions, unknowns = permissions_for_recorded_operations(["users.bulk_update"])
    assert permissions == ["userlifecycle.readwrite"]
    assert unknowns == []
    candidates, unresolved = permissions_for_sdk_calls(["bifrost.api.patch"], invocations=[{
        "call": "bifrost.api.patch", "selectors": {"path": "/api/users/bulk"},
    }])
    assert candidates == [
        "privilegedaccess.readwrite", "roleassignments.readwrite",
        "userlifecycle.readwrite", "users.readwrite",
    ]
    assert unresolved == []


def test_service_guards_resolve_static_constants_and_helper_operation_arguments() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.knowledge.get",
        "bifrost.knowledge.store",
        "bifrost.roles.assign_users",
        "bifrost.roles.assign_forms",
        "bifrost.users.update",
    ])

    assert permissions == [
        "forms.readwrite",
        "knowledge.read",
        "knowledge.readwrite",
        "privilegedaccess.readwrite",
        "roleassignments.readwrite",
        "userlifecycle.readwrite",
        "users.readwrite",
    ]
    assert unknowns == []
    candidates = sdk_named_permission_candidates([
        "bifrost.users.update",
    ])
    assert [(candidate.permission, candidate.condition) for candidate in candidates] == [
        ("privilegedaccess.readwrite", "conditional"),
        ("userlifecycle.readwrite", "conditional"),
        ("users.readwrite", "conditional"),
    ]


def test_agent_run_does_not_bind_the_builtin_set_call_to_config_set() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.agents.run"])

    assert "configs.readwrite" not in permissions
    assert "agents.execute" in permissions
    assert "users.impersonate" in permissions
    assert unknowns == []
    assert all(candidate.path != "/api/sdk/config/set" for candidate in sdk_route_candidates([
        "bifrost.agents.run",
    ]))


def test_dynamic_named_guard_argument_stays_unresolved() -> None:
    from shared import workflow_conversion_sdk as sdk

    call = ast.parse("require_permission(caller, runtime_permission, organization_id)").body[0]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)

    assert sdk._literal_permission(call.value) == (set(), True)


def test_closed_helper_cycle_keeps_its_static_guard_without_an_unknown() -> None:
    from shared import workflow_conversion_sdk as sdk

    tree = ast.parse("""
from src.services.authorization.enforce import require_permission

async def first(caller, organization_id):
    await second(caller, organization_id)

async def second(caller, organization_id):
    require_permission(caller, "configs.read", organization_id)
    await first(caller, organization_id)
""")
    functions = {function.key: function for function in sdk._functions("synthetic", tree)}
    metadata = sdk._helper_permission_metadata(
        "synthetic.first",
        functions,
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
    )

    assert metadata.candidates == (("configs.read", "unconditional"),)
    assert metadata.unresolved is False


def test_later_literal_assignment_does_not_resolve_an_earlier_dynamic_guard() -> None:
    from shared import workflow_conversion_sdk as sdk

    tree = ast.parse("""
from src.services.authorization.enforce import require_permission

async def helper(caller, permission, organization_id):
    require_permission(caller, permission, organization_id)
    permission = "configs.read"
""")
    function = next(iter(sdk._functions("synthetic", tree)))
    metadata = sdk._helper_permission_metadata(
        function.key,
        {function.key: function},
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
    )

    assert metadata.candidates == ()
    assert metadata.unresolved is True


def test_branch_selected_literal_guard_candidates_remain_conditional() -> None:
    from shared import workflow_conversion_sdk as sdk

    tree = ast.parse("""
from src.services.authorization.enforce import require_permission

async def direct(caller, choose_config, organization_id):
    if choose_config:
        permission = "configs.read"
    else:
        permission = "secrets.read"
    require_permission(caller, permission, organization_id)

async def guard(caller, permission, organization_id):
    require_permission(caller, permission, organization_id)

async def via_helper(caller, choose_config, organization_id):
    if choose_config:
        permission = "configs.read"
    else:
        permission = "secrets.read"
    await guard(caller, permission, organization_id)
""")
    functions = {function.key: function for function in sdk._functions("synthetic", tree)}
    direct = sdk._helper_permission_metadata(
        "synthetic.direct",
        functions,
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
    )
    via_helper = sdk._helper_permission_metadata(
        "synthetic.via_helper",
        functions,
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
    )

    expected = (
        ("configs.read", "conditional"),
        ("secrets.read", "conditional"),
    )
    assert direct.candidates == via_helper.candidates == expected
    assert direct.unresolved is via_helper.unresolved is False


def test_recursive_helper_collects_each_finite_argument_context() -> None:
    from shared import workflow_conversion_sdk as sdk

    tree = ast.parse("""
from src.services.authorization.enforce import require_permission

async def helper(caller, permission, organization_id):
    require_permission(caller, permission, organization_id)
    if permission == "configs.read":
        await helper(caller, "secrets.read", organization_id)
""")
    function = next(iter(sdk._functions("synthetic", tree)))
    metadata = sdk._helper_permission_metadata(
        function.key,
        {function.key: function},
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
        {"permission": {"configs.read"}},
    )

    assert metadata.candidates == (
        ("configs.read", "unconditional"),
        ("secrets.read", "conditional"),
    )
    assert metadata.unresolved is False


def test_loop_conditions_and_else_branches_preserve_helper_guards() -> None:
    from shared import workflow_conversion_sdk as sdk

    tree = ast.parse("""
from src.services.authorization.enforce import require_permission

async def configs_guard(caller, organization_id):
    require_permission(caller, "configs.read", organization_id)
    return ()

async def secrets_guard(caller, organization_id):
    require_permission(caller, "secrets.read", organization_id)
    return False

async def helper(caller, organization_id):
    for _ in configs_guard(caller, organization_id):
        break
    else:
        secrets_guard(caller, organization_id)
    while configs_guard(caller, organization_id):
        break
    else:
        secrets_guard(caller, organization_id)
""")
    functions = {function.key: function for function in sdk._functions("synthetic", tree)}
    metadata = sdk._helper_permission_metadata(
        "synthetic.helper",
        functions,
        {"synthetic": sdk._import_aliases(tree)},
        {"synthetic": sdk._static_symbols(tree)},
        False,
        set(),
    )

    assert metadata.candidates == (
        ("configs.read", "unconditional"),
        ("secrets.read", "conditional"),
    )
    assert metadata.unresolved is False


def test_invalid_static_named_permission_is_an_explicit_unknown() -> None:
    from shared import workflow_conversion_sdk as sdk

    call = ast.parse('require_permission(caller, "unknown.read", organization_id)').body[0]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)

    assert sdk._literal_permission(call.value) == (set(), True)


@pytest.mark.asyncio
async def test_config_get_service_accepts_its_proposed_named_grant(db_session) -> None:
    """The source-derived config grant passes the actual non-secret service guard."""
    from shared.sdk_config import get_sdk_config_value
    from src.core.principal import UserPrincipal
    from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
    from src.services.authorization.context import AuthorizationContext
    from src.services.authorization.enforce import Caller
    from src.services.authorization.explain import Powers, RunUser
    from src.services.authorization.runtime import RuntimeAuthority

    user_id, organization_id = uuid4(), uuid4()
    context = AuthorizationContext(
        user_id=user_id,
        home_organization_id=organization_id,
        base_role_id=uuid4(),
        base_permissions=frozenset(),
        is_external=False,
    )
    principal = UserPrincipal(
        user_id=user_id,
        email="workflow@example.test",
        organization_id=organization_id,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=uuid4(),
        run_user=RunUser(user_id, context, None, is_active=True),
        powers=Powers(
            WorkflowPermissionMode.RESTRICTED,
            (WorkflowGrant(permission="configs.read"),),
        ),
        acting_user_id=user_id,
        organization_id=organization_id,
        workflow_id=uuid4(),
        started_by_user_id=user_id,
        root_execution_id=uuid4(),
    )

    result = await get_sdk_config_value(
        db_session,
        key="missing-nonsecret-config",
        org_id=organization_id,
        caller=Caller(principal, context),
    )

    assert result is None


def test_fixed_route_wrappers_and_streaming_transport_are_candidates() -> None:
    candidates = sdk_route_candidates([
        "bifrost.ai.stream",
        "bifrost.artifacts.create_image",
        "bifrost.workflows.get",
    ])
    assert [(candidate.call, candidate.permission) for candidate in candidates] == [
        ("bifrost.ai.stream", "artifacts.read.all"),
        ("bifrost.ai.stream", "ai.execute"),
        ("bifrost.artifacts.create_image", "ai.execute"),
        ("bifrost.workflows.get", "executions.readbasic"),
    ]


def test_top_level_sdk_model_reexports_keep_their_transport_mapping() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.OAuthCredentials.refresh",
    ])
    assert permissions == ["integrations.readwrite", "secrets.read"]
    assert unknowns == []


def test_transport_free_helpers_need_no_review_but_generic_api_calls_do() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.webhooks.generate_secret",
        "bifrost.api.get",
    ])
    assert permissions == []
    assert unknowns == ["No static platform transport mapping for SDK call bifrost.api.get"]


def test_verified_transport_free_sdk_helpers_do_not_become_unknown() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.ArtifactRef.model_validate",
        "bifrost._context.register_secret",
        "bifrost.context.set_scope",
        "bifrost.files._solution_query",
    ])
    assert permissions == []
    assert unknowns == []


def test_recorded_operation_ids_and_route_templates_share_the_access_list_mapping() -> None:
    permissions, unknowns = permissions_for_recorded_operations([
        "organizations.list",
        "GET /api/organizations",
        "GET /api/forms/{id}",
        "GET /api/forms/{form_id}",
    ])
    assert permissions == ["forms.readbasic", "organizations.read"]
    assert unknowns == []


def test_recorded_route_candidates_preserve_the_observed_operation_key() -> None:
    candidates = recorded_route_candidates([
        "organizations.list",
        "GET /api/forms/{form_id}",
    ])

    assert [(candidate.call, candidate.permission) for candidate in candidates] == [
        ("GET /api/forms/{form_id}", "forms.readbasic"),
        ("organizations.list", "organizations.read"),
    ]


def test_recorded_policy_and_unrecognized_operations_do_not_become_named_grants() -> None:
    permissions, unknowns = permissions_for_recorded_operations([
        "POST /api/tables/{id}/documents/query",
        "missing.operation",
    ])
    assert permissions == []
    assert unknowns == ["No canonical permission mapping for observed operation missing.operation"]


def test_recorded_source_loader_routes_are_execution_transport_not_capabilities() -> None:
    permissions, unknowns = permissions_for_recorded_operations([
        "GET /api/sdk/modules-resolve",
        "GET /api/sdk/modules/{path:path}",
    ])
    assert permissions == []
    assert unknowns == []


def test_unknown_call_is_never_treated_as_no_permissions_needed() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.future.method"])
    assert permissions == []
    assert len(unknowns) == 1


def test_partial_transport_resolution_keeps_the_unresolved_requirement(monkeypatch) -> None:
    from shared import workflow_conversion_sdk as sdk

    call = "bifrost.example.partial"
    monkeypatch.setattr(sdk, "_sdk_bindings", lambda: (
        {call: (("GET", "/api/organizations"),)}, frozenset({call}), "a" * 64,
    ))

    permissions, unknowns = sdk.permissions_for_sdk_calls([call])

    assert permissions == ["organizations.read"]
    assert unknowns == [f"No static platform transport mapping for SDK call {call}"]


def test_sdk_contracts_describe_context_effects_without_permissions() -> None:
    context = sdk_object_contract("bifrost.context")
    assert context is not None
    assert context.sdk_type == "bifrost.ExecutionContext"
    assert {field.name: field.kind for field in context.fields}["user_id"] == "scalar"
    assert {field.name: field.kind for field in context.fields}["parameters"] == "json_object"

    set_scope = sdk_call_contract("bifrost.context.set_scope")
    assert set_scope is not None
    assert set_scope.effect == "scope_override"
    assert set_scope.return_kind == "none"

    get_context = sdk_call_contract("bifrost.get_execution_context")
    assert get_context is not None
    assert get_context.effect == "none"
    assert get_context.return_kind == "sdk_object"
    assert get_context.sdk_type == "bifrost.ExecutionContext"


def test_sdk_contracts_preserve_known_object_and_credential_chains() -> None:
    integration = sdk_call_contract("bifrost.integrations.get")
    assert integration is not None
    assert integration.return_kind == "sdk_object"
    assert integration.sdk_type == "bifrost.IntegrationData"

    integration_data = sdk_object_contract(integration.sdk_type)
    assert integration_data is not None
    oauth = next(field for field in integration_data.fields if field.name == "oauth")
    assert oauth.kind == "sdk_credential"
    assert oauth.sdk_type == "bifrost.OAuthCredentials"

    refresh = sdk_call_contract("bifrost.OAuthCredentials.refresh")
    assert refresh is not None
    assert refresh.return_kind == "sdk_credential"
    assert refresh.sdk_type == "bifrost.OAuthCredentials"

    runtime_log = sdk_call_contract("bifrost._runtime_io.write_log")
    assert runtime_log is not None
    assert runtime_log.effect == "runtime_log"
    assert runtime_log.return_kind == "scalar"


def test_sdk_contracts_preserve_table_document_json_fields() -> None:
    document = sdk_call_contract("bifrost.tables.get")
    assert document is not None
    assert document.return_kind == "sdk_object"
    assert document.sdk_type == "bifrost.DocumentData"

    document_data = sdk_object_contract(document.sdk_type)
    assert document_data is not None
    data = next(field for field in document_data.fields if field.name == "data")
    assert data.kind == "json_object"


def test_client_transport_facades_bind_literal_routes_without_factory_grants() -> None:
    client_factory = sdk_call_contract("bifrost.client.get_client")
    assert client_factory is not None
    assert client_factory.effect == "none"
    assert client_factory.return_kind == "sdk_object"
    assert client_factory.sdk_type == "bifrost.client.BifrostClient"

    response_helper = sdk_call_contract("bifrost.client.raise_for_status_with_detail")
    assert response_helper is not None
    assert response_helper.effect == "none"
    assert response_helper.return_kind == "none"

    engine_request = sdk_call_contract("bifrost.client.engine_request")
    assert engine_request is not None
    assert engine_request.parameters[:2] == ("method", "path")
    assert sdk_call_contract("bifrost.client.post") is not None

    permissions, unknowns = permissions_for_sdk_calls(
        [
            "bifrost.client.get_client",
            "bifrost.client.engine_request",
            "bifrost.client.post",
            "bifrost.client.raise_for_status_with_detail",
        ],
        invocations=[
            {
                "call": "bifrost.client.engine_request",
                "selectors": {"http_method": "POST", "path": "/api/sdk/config/get"},
                "dynamic_selectors": [],
            },
            {
                "call": "bifrost.client.post",
                "selectors": {"path": "/api/files/write", "location": "workspace"},
                "dynamic_selectors": [],
            },
        ],
    )

    assert permissions == ["configs.read", "repository.readwrite", "secrets.read"]
    assert unknowns == []


def test_client_transport_with_dynamic_method_or_path_remains_unknown() -> None:
    permissions, unknowns = permissions_for_sdk_calls(
        ["bifrost.client.engine_request", "bifrost.client.post"],
        invocations=[
            {
                "call": "bifrost.client.engine_request",
                "selectors": {"path": "/api/sdk/config/get"},
                "dynamic_selectors": ["http_method"],
            },
            {
                "call": "bifrost.client.post",
                "selectors": {},
                "dynamic_selectors": ["path"],
            },
        ],
    )

    assert permissions == []
    assert unknowns == [
        "No static platform transport mapping for SDK call bifrost.client.engine_request",
        "No static platform transport mapping for SDK call bifrost.client.post",
    ]


def test_client_sync_shortcut_and_signed_url_method_use_distinct_http_verbs() -> None:
    permissions, unknowns = permissions_for_sdk_calls(
        ["bifrost.client.get_sync", "bifrost.client.engine_request"],
        invocations=[
            {
                "call": "bifrost.client.get_sync",
                "selectors": {"path": "/api/organizations"},
                "dynamic_selectors": [],
            },
            {
                "call": "bifrost.client.engine_request",
                "selectors": {
                    "http_method": "POST",
                    "path": "/api/files/signed-url",
                    "method": "GET",
                    "location": "workspace",
                },
                "dynamic_selectors": [],
            },
        ],
    )

    assert permissions == ["organizations.read", "repository.read"]
    assert unknowns == []


@pytest.mark.parametrize("selectors,expected", [
    ({"path": "/api/files/complete-upload", "location": "workspace"}, ["repository.readwrite"]),
    ({"path": "/api/files/complete-upload"}, []),
])
def test_client_upload_completion_uses_location_selected_named_permission(selectors, expected) -> None:
    permissions, unknowns = permissions_for_sdk_calls(
        ["bifrost.client.post"],
        invocations=[{
            "call": "bifrost.client.post",
            "selectors": selectors,
            "dynamic_selectors": [],
        }],
    )

    assert permissions == expected
    assert unknowns == []


def test_proven_pure_public_constructor_contracts_need_no_permissions() -> None:
    calls = [
        "bifrost.UserError",
        "bifrost.WorkflowError",
        "bifrost.ValidationError",
        "bifrost.IntegrationError",
        "bifrost.ConfigurationError",
        "bifrost.AIInputFile",
    ]

    permissions, unknowns = permissions_for_sdk_calls(calls)

    assert permissions == []
    assert unknowns == []
    user_error = sdk_call_contract("bifrost.UserError")
    assert user_error is not None
    assert user_error.return_kind == "sdk_object"
    assert user_error.sdk_type == "bifrost.UserError"
    error_object = sdk_object_contract("bifrost.UserError")
    assert error_object is not None
    assert error_object.fields == ()
    input_file = sdk_call_contract("bifrost.AIInputFile")
    assert input_file is not None
    assert input_file.return_kind == "sdk_object"
    assert input_file.sdk_type == "bifrost.AIInputFile"


def test_sdk_contracts_keep_formal_resource_selector_parameters() -> None:
    files_read = sdk_call_contract("bifrost.files.read")
    assert files_read is not None
    assert files_read.parameters == ("path", "location", "mode", "scope", "solution")

    tables_query = sdk_call_contract("bifrost.tables.query")
    assert tables_query is not None
    assert tables_query.parameters[:2] == ("table", "where")
    assert "scope" in tables_query.parameters
    assert "solution" in tables_query.parameters

    assert files_read.resource_defaults == (
        ("location", "workspace"),
        ("scope", None),
        ("solution", None),
    )

    signed_url = sdk_call_contract("bifrost.files.get_signed_url")
    assert signed_url is not None
    assert signed_url.resource_defaults == (
        ("method", "PUT"),
        ("location", "uploads"),
        ("scope", None),
    )


def test_catalog_and_sdk_version_participate_in_proposal_versioning() -> None:
    assert len(sdk_permission_fingerprint()) == 64


def test_sdk_clients_and_credentials_are_distinct_from_data_objects() -> None:
    assert sdk_object_is_data("bifrost.DocumentData")
    assert sdk_object_is_data("bifrost.WorkflowExecution")
    assert not sdk_object_is_data("bifrost.OAuthCredentials")
    assert not sdk_object_is_data("bifrost.client.BifrostClient")
    assert not sdk_object_is_data("bifrost.ExecutionContext")
