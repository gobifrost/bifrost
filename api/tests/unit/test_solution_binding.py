from pathlib import Path

import pytest

from bifrost.solution_binding import (
    SolutionBinding,
    SolutionBindingError,
    binding_from_install,
    read_solution_binding,
    resolve_install_ref,
    write_solution_binding,
)


def test_write_solution_binding_merges_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("BIFROST_API_URL=http://api\nOTHER=value\n")

    write_solution_binding(
        tmp_path,
        SolutionBinding(
            solution_id="11111111-1111-1111-1111-111111111111",
            slug="dispatch",
            organization_id="22222222-2222-2222-2222-222222222222",
            scope="org",
        ),
    )

    text = env.read_text()
    assert "BIFROST_API_URL=http://api\n" in text
    assert "OTHER=value\n" in text
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n" in text
    assert "BIFROST_SOLUTION_SLUG=dispatch\n" in text
    assert "BIFROST_SOLUTION_ORG_ID=22222222-2222-2222-2222-222222222222\n" in text
    assert "BIFROST_SOLUTION_SCOPE=org\n" in text


def test_write_solution_binding_replaces_existing_solution_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "BIFROST_SOLUTION_ID=old-id\n"
        "BIFROST_SOLUTION_SLUG=old-slug\n"
        "OTHER=value\n"
    )

    write_solution_binding(
        tmp_path,
        SolutionBinding(
            solution_id="11111111-1111-1111-1111-111111111111",
            slug="dispatch",
            organization_id="22222222-2222-2222-2222-222222222222",
            scope="org",
        ),
    )

    text = env.read_text()
    assert "OTHER=value\n" in text
    assert "old-id" not in text
    assert "old-slug" not in text
    assert text.count("BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n") == 1
    assert text.count("BIFROST_SOLUTION_SLUG=dispatch\n") == 1
    assert text.count("BIFROST_SOLUTION_ORG_ID=22222222-2222-2222-2222-222222222222\n") == 1
    assert text.count("BIFROST_SOLUTION_SCOPE=org\n") == 1


def test_read_solution_binding_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_solution_binding(tmp_path) is None


def test_read_solution_binding_returns_none_when_org_scope_missing_org_id(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=dispatch\n"
        "BIFROST_SOLUTION_ORG_ID=\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )

    assert read_solution_binding(tmp_path) is None


def test_read_solution_binding_parses_global_scope(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=dispatch\n"
        "BIFROST_SOLUTION_ORG_ID=\n"
        "BIFROST_SOLUTION_SCOPE=global\n"
    )

    binding = read_solution_binding(tmp_path)

    assert binding is not None
    assert binding.solution_id == "11111111-1111-1111-1111-111111111111"
    assert binding.slug == "dispatch"
    assert binding.organization_id is None
    assert binding.scope == "global"


def test_binding_from_install_rejects_slug_mismatch() -> None:
    with pytest.raises(SolutionBindingError, match="does not match descriptor slug"):
        binding_from_install(
            {"id": "i", "slug": "other", "organization_id": None},
            descriptor_slug="expected",
        )


def test_binding_from_install_rejects_missing_id() -> None:
    with pytest.raises(SolutionBindingError, match="missing id"):
        binding_from_install(
            {"slug": "expected", "organization_id": None},
            descriptor_slug="expected",
        )


def test_binding_from_install_global() -> None:
    binding = binding_from_install(
        {"id": "i", "slug": "expected", "organization_id": None},
        descriptor_slug="expected",
    )
    assert binding.scope == "global"
    assert binding.organization_id is None


def test_resolve_install_ref_resolves_by_id() -> None:
    installs = [
        {"id": "a", "slug": "other", "organization_id": "org-a"},
        {"id": "b", "slug": "expected", "organization_id": "org-b"},
    ]

    binding = resolve_install_ref(installs, "b", descriptor_slug="expected")

    assert binding.solution_id == "b"
    assert binding.slug == "expected"
    assert binding.organization_id == "org-b"
    assert binding.scope == "org"


def test_resolve_install_ref_resolves_by_unique_slug() -> None:
    installs = [
        {"id": "a", "slug": "other", "organization_id": "org-a"},
        {"id": "b", "slug": "expected", "organization_id": None},
    ]

    binding = resolve_install_ref(installs, "expected", descriptor_slug="expected")

    assert binding.solution_id == "b"
    assert binding.slug == "expected"
    assert binding.organization_id is None
    assert binding.scope == "global"


def test_resolve_install_ref_prefers_id_over_slug() -> None:
    installs = [
        {"id": "expected", "slug": "descriptor", "organization_id": "org-a"},
        {"id": "b", "slug": "expected", "organization_id": "org-b"},
    ]

    binding = resolve_install_ref(installs, "expected", descriptor_slug="descriptor")

    assert binding.solution_id == "expected"
    assert binding.slug == "descriptor"
    assert binding.organization_id == "org-a"
    assert binding.scope == "org"


def test_resolve_install_ref_rejects_ambiguous_slug() -> None:
    installs = [
        {"id": "a", "slug": "expected", "organization_id": "org-a"},
        {"id": "b", "slug": "expected", "organization_id": "org-b"},
    ]
    with pytest.raises(SolutionBindingError, match="multiple installs"):
        resolve_install_ref(installs, "expected", descriptor_slug="expected")
