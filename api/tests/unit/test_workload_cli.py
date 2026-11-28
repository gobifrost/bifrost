"""Workload login options never fall through into a human login flow."""

import pytest

from bifrost import cli


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--subject-token-type", "id_token"], "requires --subject-token-file"),
        (["--subject-token-file", "assertion.jwt"], "requires --subject-token-type"),
        (["--managed-identity-client-id", "client-id"], "requires --managed-identity"),
        (["--audience", "api://resource"], "requires --oidc or --managed-identity"),
        (["--oidc", "--subject-token-type", "id_token"], "requires --subject-token-file"),
    ],
)
def test_orphaned_workload_option_is_a_usage_error_not_human_login(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str], message: str,
) -> None:
    monkeypatch.setattr(cli, "login_flow", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("human login")))

    assert cli.handle_login(args) == 1
    assert message in capsys.readouterr().err
