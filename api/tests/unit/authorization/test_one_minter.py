"""One minter: no access token is signed outside a reviewed set of places (plan §9.3).

Run access tokens come from ``tokens.issue_access_token``, which signs
through ``tokens._encode_claims``. These are blunt, file-level rules that
fail closed. They catch accidental regressions. Deliberate evasion (a
dynamic ``getattr``, or leaking a signer out of an approved function through
a global or closure) is out of scope: code review and the runtime validator,
which refuses any token the database does not back, cover it.

- only reviewed modules may import PyJWT, in any form and at any scope, and
  only as a plain ``import jwt``; no module imports a name ``jwt`` from
  anywhere else or reaches one as an attribute (``security.jwt``); inside the
  reviewed modules ``jwt`` is used only as ``jwt.<attribute>`` from the
  module's reviewed attributes, and ``jwt.encode`` only in its reviewed
  functions, matched by exact qualified name;
- only the tokens module may name ``_encode_claims`` at all, and only inside
  the two token issuers (exact qualified names); likewise ``_encode_legacy``,
  only inside the legacy runtime minter (token PR1c deletes both);
- the legacy runtime minter ``_legacy_runtime_token`` is named only by the
  run-credential issuer and the legacy service rotation;
- ``create_access_token`` is used only by reviewed functions, and never
  imported under another name. Those are embed tokens and impersonation
  markers (which keep their own formats), the MCP bridge's people branch,
  and the people sites PR2 moves to ``issue_access_token``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[3]
_SCANNED = ("src", "shared", "bifrost")
_TOKENS_MODULE = "src.services.authorization.tokens"

# Module -> (jwt attributes it may use, functions that may call jwt.encode).
_JWT_MODULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # Access, refresh and MFA tokens.
    "src.core.security": (
        frozenset({"encode", "decode", "ExpiredSignatureError", "InvalidTokenError"}),
        frozenset({"create_access_token", "create_refresh_token", "create_mfa_token"}),
    ),
    # The run-token minter, and the legacy runtime format until token PR1c.
    _TOKENS_MODULE: (frozenset({"encode"}), frozenset({"_encode_claims", "_encode_legacy"})),
    # Local Solution development context tokens.
    "shared.solution_dev": (frozenset({"encode"}), frozenset({"_mint_solution_dev_token"})),
    # OAuth state parameters.
    "src.services.oauth_state_core": (
        frozenset({"encode", "decode", "ExpiredSignatureError", "InvalidTokenError"}),
        frozenset({"encode_state_jwt"}),
    ),
    # Decode only.
    "shared.browser_impersonation": (frozenset({"decode", "InvalidTokenError"}), frozenset()),
    "src.services.oauth_sso": (frozenset({"decode", "PyJWTError"}), frozenset()),
    "src.services.webhooks.adapters.microsoft_bot_framework": (
        frozenset({"decode", "get_unverified_header", "PyJWK", "PyJWTError"}),
        frozenset(),
    ),
}

_ENCODE_CLAIMS_USERS = frozenset({"issue_access_token", "issue_run_refresh_token"})
_ENCODE_LEGACY_USERS = frozenset({"_legacy_runtime_token"})
_LEGACY_MINTER_SITES = {
    f"{_TOKENS_MODULE}:issue_run_credentials",
    "src.services.service_claim:ServiceClaimLoop._rotate_token",
}

_CREATE_ACCESS_TOKEN_SITES = {
    # Their own formats, unchanged by the token program.
    "src.core.security:create_embed_access_token",
    "shared.browser_impersonation:create_browser_impersonation",
    # The MCP bridge's people branch (PR2); its runtime branch uses
    # issue_access_token.
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


def _scopes(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """Every node with the qualified name of its enclosing function or class."""
    found: list[tuple[ast.AST, str]] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            found.append((child, ".".join(scope) or "<module>"))
            inner = (*scope, child.name) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
            walk(child, inner)

    walk(tree, ())
    return found


def _jwt_violations(module: str, source: str) -> list[str]:
    tree = ast.parse(source)
    allowed = _JWT_MODULES.get(module)
    problems: list[str] = []
    jwt_attribute_bases: set[int] = set()
    for node, scope in _scopes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "jwt" and (allowed is None or alias.name != "jwt" or alias.asname):
                    problems.append(f"{module}:{scope} imports {alias.name} as {alias.asname or alias.name}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "jwt" and not node.level:
            problems.append(f"{module}:{scope} imports from {node.module}")
        elif isinstance(node, ast.ImportFrom) and any(alias.name == "jwt" for alias in node.names):
            # A re-export of PyJWT through another module.
            problems.append(f"{module}:{scope} imports jwt from {node.module or '.'}")
        elif isinstance(node, ast.Attribute) and node.attr == "jwt":
            problems.append(f"{module}:{scope} reaches jwt as an attribute")
        elif allowed is not None and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "jwt":
            jwt_attribute_bases.add(id(node.value))
            attributes, encoders = allowed
            if node.attr not in attributes:
                problems.append(f"{module}:{scope} uses jwt.{node.attr}")
            elif node.attr == "encode" and scope not in encoders:
                problems.append(f"{module}:{scope} signs with jwt.encode")
    if allowed is not None:
        for node, scope in _scopes(tree):
            if isinstance(node, ast.Name) and node.id == "jwt" and id(node) not in jwt_attribute_bases:
                problems.append(f"{module}:{scope} uses the jwt module as a value")
    return problems


def _names(node: ast.AST, name: str) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Attribute) and node.attr == name)
        or (isinstance(node, ast.alias) and name in (node.name, node.asname))
    )


def _encode_claims_violations(module: str, source: str) -> list[str]:
    problems: list[str] = []
    for node, scope in _scopes(ast.parse(source)):
        for signer, users in (("_encode_claims", _ENCODE_CLAIMS_USERS), ("_encode_legacy", _ENCODE_LEGACY_USERS)):
            if _names(node, signer) and (module != _TOKENS_MODULE or scope not in users):
                problems.append(f"{module}:{scope} names {signer}")
    return problems


def _legacy_minter_sites(module: str, source: str) -> set[str]:
    return {
        f"{module}:{scope}"
        for node, scope in _scopes(ast.parse(source))
        if _names(node, "_legacy_runtime_token") and not (module == _TOKENS_MODULE and scope == "_legacy_runtime_token")
    }


def _create_access_token_sites(module: str, source: str) -> tuple[set[str], list[str]]:
    sites: set[str] = set()
    problems: list[str] = []
    for node, scope in _scopes(ast.parse(source)):
        if isinstance(node, ast.alias) and node.name.split(".")[-1] == "create_access_token" and node.asname:
            problems.append(f"{module}:{scope} imports create_access_token as {node.asname}")
        elif (isinstance(node, ast.Name) and node.id == "create_access_token") or (
            isinstance(node, ast.Attribute) and node.attr == "create_access_token"
        ):
            sites.add(f"{module}:{scope}")
    return sites, problems


def _modules() -> list[tuple[str, str]]:
    found = []
    for package in _SCANNED:
        for path in sorted((_API_ROOT / package).rglob("*.py")):
            parts = path.relative_to(_API_ROOT).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            found.append((module, path.read_text()))
    return found


def _imports_jwt(source: str) -> bool:
    return any(
        alias.name.split(".")[0] == "jwt"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    )


def test_only_reviewed_modules_use_pyjwt() -> None:
    modules = _modules()

    assert [problem for module, source in modules for problem in _jwt_violations(module, source)] == []
    # No stale entry: every reviewed module still imports PyJWT.
    assert {module for module, source in modules if _imports_jwt(source)} == set(_JWT_MODULES)


def test_only_the_token_issuers_name_the_run_token_signer() -> None:
    assert [problem for module, source in _modules() for problem in _encode_claims_violations(module, source)] == []


def test_only_reviewed_functions_mint_legacy_runtime_tokens() -> None:
    sites = {site for module, source in _modules() for site in _legacy_minter_sites(module, source)}
    assert sites == _LEGACY_MINTER_SITES


def test_only_reviewed_functions_mint_access_tokens_outside_the_minter() -> None:
    sites: set[str] = set()
    problems: list[str] = []
    for module, source in _modules():
        found, wrong = _create_access_token_sites(module, source)
        sites |= found
        problems += wrong

    assert problems == []
    assert sites == _CREATE_ACCESS_TOKEN_SITES


def test_each_known_bypass_is_flagged() -> None:
    outside = "src.services.sneaky"
    jwt_cases = {
        # Outside the reviewed modules: any import, any form, any scope.
        (outside, "import jwt\n"),
        (outside, "import jwt as signing\n"),
        (outside, "from jwt import encode\n"),
        (outside, "from jwt.api_jwt import PyJWT\n"),
        (outside, "def f():\n    import jwt\n    return jwt.encode({}, 'k')\n"),
        # Shadowing elsewhere in the file does not hide the import.
        (outside, "import jwt as signing\ndef g():\n    import json as signing\n    return signing\n"),
        # Inside a reviewed module: aliases, value escapes, unreviewed
        # attributes and unreviewed signing functions.
        ("shared.browser_impersonation", "import jwt as signing\n"),
        ("shared.browser_impersonation", "import jwt\ndef f(c):\n    signing = jwt\n    return signing.encode(c, 'k')\n"),
        ("shared.browser_impersonation", "import jwt\ndef f(c):\n    signer = jwt.PyJWT()\n    return signer.encode(c, 'k')\n"),
        ("shared.browser_impersonation", "import jwt\ndef f(c):\n    return jwt.encode(c, 'k')\n"),
        ("src.core.security", "import jwt\ndef sneaky(c):\n    return jwt.encode(c, 'k')\n"),
        # Re-exports of PyJWT through a reviewed module.
        (outside, "from src.core.security import jwt\ndef f(c):\n    return jwt.encode(c, 'k')\n"),
        (outside, "from src.core import security\ndef f(c):\n    return security.jwt.encode(c, 'k')\n"),
        # Approval is by exact qualified name, not the final name.
        (
            "src.core.security",
            "import jwt\nclass Other:\n    def create_access_token(self, c):\n        return jwt.encode(c, 'k')\n",
        ),
    }
    for module, source in jwt_cases:
        assert _jwt_violations(module, source), (module, source)

    encode_claims_cases = {
        (outside, "from src.services.authorization.tokens import _encode_claims\n"),
        (outside, "from src.services.authorization import tokens\ndef f(c):\n    return tokens._encode_claims(c)\n"),
        (outside, "from src.services.authorization import tokens\nt = tokens\ndef f(c):\n    return t._encode_claims(c)\n"),
        (_TOKENS_MODULE, "def another(c):\n    return _encode_claims(c)\n"),
        (_TOKENS_MODULE, "sign = _encode_claims\n"),
        (_TOKENS_MODULE, "class Other:\n    def issue_access_token(self, c):\n        return _encode_claims(c)\n"),
        (_TOKENS_MODULE, "def issue_access_token(c):\n    return _encode_legacy(c, 60)\n"),
        (outside, "from src.services.authorization.tokens import _encode_legacy\n"),
    }
    for module, source in encode_claims_cases:
        assert _encode_claims_violations(module, source), (module, source)

    aliased = "from src.core.security import create_access_token as cat\ndef f():\n    return cat({})\n"
    assert _create_access_token_sites(outside, aliased)[1]
    called = "from src.core import security\ndef f():\n    return security.create_access_token({})\n"
    assert _create_access_token_sites(outside, called)[0] == {f"{outside}:f"}


def test_reviewed_uses_pass() -> None:
    assert _jwt_violations("shared.browser_impersonation", "import jwt\ndef f(t):\n    return jwt.decode(t, 'k')\n") == []
    assert _encode_claims_violations(_TOKENS_MODULE, "def issue_access_token(c):\n    return _encode_claims(c)\n") == []
    assert _jwt_violations(
        "src.services.sneaky", "import json as signing\ndef f(x):\n    return signing.dumps(x).encode()\n"
    ) == []


def _principal_from_claims_violations(module: str, source: str) -> list[str]:
    """Only the tokens module turns claims into a principal (plan §6 rule 2)."""
    if module == _TOKENS_MODULE:
        return []
    return [
        f"{module}:{scope} names principal_from_claims"
        for node, scope in _scopes(ast.parse(source))
        if (isinstance(node, ast.Name) and node.id == "principal_from_claims")
        or (isinstance(node, ast.Attribute) and node.attr == "principal_from_claims")
        or (isinstance(node, ast.alias) and "principal_from_claims" in (node.name, node.asname))
    ]


def test_only_the_tokens_module_turns_claims_into_a_principal() -> None:
    assert [problem for module, source in _modules() for problem in _principal_from_claims_violations(module, source)] == []
    outside = "src.core.sneaky"
    for source in (
        "from src.services.authorization.tokens import principal_from_claims\n",
        "from src.services.authorization import tokens\ndef f(c):\n    return tokens.principal_from_claims(c)\n",
    ):
        assert _principal_from_claims_violations(outside, source), source
