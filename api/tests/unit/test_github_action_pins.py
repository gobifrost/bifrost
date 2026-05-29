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
