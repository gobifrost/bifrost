from __future__ import annotations

from pathlib import Path

from scripts import check_github_action_pins


def test_flags_external_actions_without_full_sha(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "\n".join(
            [
                "steps:",
                "  - uses: actions/checkout@v6",
                "  - uses: owner/action@main",
                "  - uses: owner/no-ref",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert [violation.action for violation in violations] == [
        "actions/checkout@v6",
        "owner/action@main",
        "owner/no-ref",
    ]


def test_allows_sha_pinned_local_and_docker_actions(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "\n".join(
            [
                "steps:",
                "  - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
                "  - uses: ./.github/actions/local-action",
                "  - uses: docker://ghcr.io/example/image:latest",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert violations == []


def test_flags_sha_that_does_not_match_version_comment(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "steps:\n"
        "  - uses: owner/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.2.3\n",
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow],
        lambda repository, version: "b" * 40,
    )

    assert len(violations) == 1
    assert "owner/action@v1.2.3 resolves to" in violations[0].reason


def test_allows_sha_matching_version_comment_and_caches_resolution(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow.yml"
    sha = "a" * 40
    workflow.write_text(
        "steps:\n"
        f"  - uses: owner/action@{sha} # v1.2.3\n"
        f"  - uses: owner/action/subpath@{sha} # v1.2.3\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def resolve(repository: str, version: str) -> str:
        calls.append((repository, version))
        return sha

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow], resolve
    )

    assert violations == []
    assert calls == [("owner/action", "v1.2.3")]
