"""Builder runtime profile should be shared, transient, and target-aware."""

from types import SimpleNamespace
from uuid import uuid4

from src.services.builder.agent_identity import (
    bind_builder_tool_arguments,
    build_builder_runtime_profile,
    sanitize_builder_tool_parameters,
)


def _solution(*, name: str, organization_id=None, owner_user_id=None) -> object:
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
    )


def test_builder_runtime_profile_identity_is_shared() -> None:
    organization_id = uuid4()
    alpha = build_builder_runtime_profile(
        _solution(name="Alpha", organization_id=organization_id)
    )
    beta = build_builder_runtime_profile(_solution(name="Beta"))

    assert alpha.id == beta.id
    assert alpha.bundle_path == "skills/bifrost-build"
    assert alpha.name == "Alpha Builder"
    assert alpha.authorization_boundary == f"organization:{organization_id}"
    assert beta.name == "Beta Builder"
    assert alpha.system_tools == beta.system_tools


def test_global_builder_runtime_profile_uses_target_specific_tools() -> None:
    authorization = SimpleNamespace(
        effective_capabilities=frozenset({"platform.superuser"}),
    )
    profile = build_builder_runtime_profile(
        _solution(name="Global", organization_id=uuid4()),
        target_kind="global_repo",
        authorization=authorization,
    )

    assert profile.bundle_path == "skills/bifrost-build"
    assert profile.skill_asset_root is not None
    assert "TARGET: GLOBAL WORKSPACE" in profile.system_prompt
    assert "list_files" in profile.system_tools
    assert "apply_patch" in profile.system_tools
    assert "stage_global_operation_change" in profile.system_tools
    assert "discard_global_operation_change" in profile.system_tools
    assert "apply_global_operation_changes" not in profile.system_tools
    assert "rollback_global_operation_changes" not in profile.system_tools
    assert "bifrost_list_roles" in profile.system_tools
    assert "bifrost_create_role" not in profile.system_tools
    assert "bifrost_write_file" not in profile.system_tools
    assert "validate_solution" not in profile.system_tools
    assert "test_solution_build" not in profile.system_tools
    assert profile.name == "Global Workspace Builder"
    assert profile.description == "Administrator global workspace proposal agent"
    assert profile.authorization_boundary == "platform"


def test_organization_profile_exposes_only_allowed_domain_operations() -> None:
    organization_id = uuid4()
    capabilities = {"agents.read", "agents.readwrite", "repository.readwrite"}
    authorization = SimpleNamespace(
        effective_capabilities=frozenset(capabilities),
    )

    profile = build_builder_runtime_profile(
        _solution(name="Customer", organization_id=organization_id),
        target_kind="organization",
        authorization=authorization,
    )

    assert profile.target_kind == "organization"
    assert profile.authorization_boundary == f"organization:{organization_id}"
    assert "bifrost_list_agents" in profile.system_tools
    assert "bifrost_create_agent" in profile.system_tools
    assert "bifrost_write_file" not in profile.system_tools
    assert "list_files" not in profile.system_tools
    assert all(not tool.startswith("bifrost_create_role") for tool in profile.system_tools)


def test_direct_builder_tool_schema_hides_boundary_selectors() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "scope": {"type": "string"},
            "organization_id": {"type": "string"},
            "authorization_boundary": {"type": "string"},
        },
        "required": ["name", "scope", "authorization_boundary"],
    }

    sanitized = sanitize_builder_tool_parameters(schema)

    assert set(sanitized["properties"]) == {"name"}
    assert sanitized["required"] == ["name"]
    assert set(schema["properties"]) == {
        "name",
        "scope",
        "organization_id",
        "authorization_boundary",
    }


def test_direct_builder_tool_arguments_bind_to_organization_boundary() -> None:
    organization_id = uuid4()
    bound = bind_builder_tool_arguments(
        {
            "name": "Expense Agent",
            "scope": "global",
            "organization_id": str(uuid4()),
            "authorization_boundary": "platform",
        },
        parameters={
            "properties": {
                "name": {"type": "string"},
                "scope": {"type": "string"},
                "organization_id": {"type": "string"},
                "authorization_boundary": {"type": "string"},
            },
        },
        target_kind="organization",
        organization_id=organization_id,
        authorization_boundary=f"organization:{organization_id}",
    )

    assert bound == {
        "name": "Expense Agent",
        "scope": str(organization_id),
        "organization_id": str(organization_id),
        "authorization_boundary": f"organization:{organization_id}",
    }


def test_direct_builder_tool_arguments_bind_to_global_boundary() -> None:
    bound = bind_builder_tool_arguments(
        {
            "name": "Platform Role",
            "scope": str(uuid4()),
            "organization_id": str(uuid4()),
            "authorization_boundary": "organization:escape",
        },
        parameters={
            "properties": {
                "name": {"type": "string"},
                "scope": {"type": "string"},
                "organization_id": {"type": "string"},
                "authorization_boundary": {"type": "string"},
            },
        },
        target_kind="global_repo",
        organization_id=None,
        authorization_boundary="platform",
    )

    assert bound == {
        "name": "Platform Role",
        "scope": "global",
        "organization_id": None,
        "authorization_boundary": "platform",
    }
