"""One minter: no access token is signed outside a reviewed set of functions (plan §9.3).

Run access tokens come from ``tokens.issue_access_token``, which signs
through ``tokens._encode_claims``. The remaining ``create_access_token``
callers are embed tokens and impersonation markers (which keep their own
formats), the runtime minters PR1b replaces, and the people sites PR2 moves
to ``issue_access_token``. A new signing site fails this test until it is
reviewed here.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[3]
_SCANNED = ("src", "shared", "bifrost")

_JWT_ENCODE_CALLERS = {
    "src.services.authorization.tokens:_encode_claims",
    "src.core.security:create_access_token",
    "src.core.security:create_refresh_token",
    "src.core.security:create_mfa_token",
    "shared.solution_dev:_mint_solution_dev_token",
    "src.services.oauth_state_core:encode_state_jwt",
}

_CREATE_ACCESS_TOKEN_CALLERS = {
    # Their own formats, unchanged by the token program.
    "src.core.security:create_embed_access_token",
    "shared.browser_impersonation:create_browser_impersonation",
    # Runtime minters: PR1b switches them to issue_access_token.
    "src.core.security:mint_engine_token",
    "src.core.security:mint_service_token",
    # PR1b (runtime branch) and PR2 (people branch).
    "src.services.mcp_server.tools._http_bridge:_token_from_context",
    # People: PR2 moves these to issue_access_token.
    "src.routers.auth:_generate_login_tokens",
    "src.routers.auth:mfa_initial_verify",
    "src.routers.auth:refresh_token",
    "src.routers.auth:setup_passkey_verify",
    "src.routers.mfa:verify_mfa",
    "src.routers.oauth_sso:oauth_callback",
    "src.services.mcp_server.auth:BifrostAuthProvider._token",
}


def _is_jwt_encode(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "encode"
        and isinstance(func.value, ast.Name)
        and func.value.id == "jwt"
    )


def _is_create_access_token(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "create_access_token") or (
        isinstance(func, ast.Attribute) and func.attr == "create_access_token"
    )


def _callers(module: str, source: str, matches: Callable[[ast.Call], bool]) -> set[str]:
    """``module:qualname`` of every function whose body makes a matching call."""
    found: set[str] = set()
    scope: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter

        def visit_Call(self, node: ast.Call) -> None:
            if matches(node):
                found.add(f"{module}:{'.'.join(scope) or '<module>'}")
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def _scan(matches: Callable[[ast.Call], bool]) -> set[str]:
    found: set[str] = set()
    for package in _SCANNED:
        for path in sorted((_API_ROOT / package).rglob("*.py")):
            module = ".".join(path.relative_to(_API_ROOT).with_suffix("").parts)
            found |= _callers(module, path.read_text(), matches)
    return found


def test_only_reviewed_functions_sign_jwts() -> None:
    assert _scan(_is_jwt_encode) == _JWT_ENCODE_CALLERS


def test_only_reviewed_functions_mint_access_tokens_outside_the_minter() -> None:
    assert _scan(_is_create_access_token) == _CREATE_ACCESS_TOKEN_CALLERS


def test_a_new_signing_site_is_flagged() -> None:
    source = (
        "import jwt\n"
        "def sneaky(claims):\n"
        "    return jwt.encode(claims, 'k', algorithm='HS256')\n"
        "class Minter:\n"
        "    def mint(self):\n"
        "        return create_access_token({'sub': 'x'})\n"
    )

    assert _callers("src.services.sneaky", source, _is_jwt_encode) == {"src.services.sneaky:sneaky"}
    assert _callers("src.services.sneaky", source, _is_create_access_token) == {"src.services.sneaky:Minter.mint"}
