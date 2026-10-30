"""One minter: no access token is signed outside a reviewed set of functions (plan §9.3).

Run access tokens come from ``tokens.issue_access_token``, which signs
through ``tokens._encode_claims``. The remaining ``create_access_token``
callers are embed tokens and impersonation markers (which keep their own
formats), the runtime minters PR1b replaces, and the people sites PR2 moves
to ``issue_access_token``. A new signing site fails this test until it is
reviewed here.

Names are resolved through each module's imports, so ``import jwt as x``,
``from jwt import encode``, ``jwt.PyJWT().encode`` and an aliased
``create_access_token`` are all seen, and a reference counts as well as a
call (``sign = jwt.encode``). Dynamic lookups (``getattr(jwt, "encode")``)
are out of reach of a static check.
"""

from __future__ import annotations

import ast
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[3]
_SCANNED = ("src", "shared", "bifrost")

# Every way the PyJWT package signs.
_JWT_SIGNERS = frozenset(
    {
        "jwt.encode",
        "jwt.api_jwt.encode",
        "jwt.api_jws.encode",
        "jwt.PyJWT().encode",
        "jwt.PyJWS().encode",
        "jwt.api_jwt.PyJWT().encode",
        "jwt.api_jws.PyJWS().encode",
    }
)
_CREATE_ACCESS_TOKEN = "src.core.security.create_access_token"
_ENCODE_CLAIMS = "src.services.authorization.tokens._encode_claims"

_JWT_SIGNING_SITES = {
    "src.services.authorization.tokens:_encode_claims",
    "src.core.security:create_access_token",
    "src.core.security:create_refresh_token",
    "src.core.security:create_mfa_token",
    "shared.solution_dev:_mint_solution_dev_token",
    "src.services.oauth_state_core:encode_state_jwt",
}

_CREATE_ACCESS_TOKEN_SITES = {
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

# The run-token signer is reached only through the two token issuers.
_ENCODE_CLAIMS_SITES = {
    "src.services.authorization.tokens:issue_access_token",
    "src.services.authorization.tokens:issue_run_refresh_token",
}


def _bindings(module: str, tree: ast.Module, is_package: bool) -> dict[str, str]:
    """Local name -> fully qualified origin, from every import in the module
    (function-level imports included) and the module's own top-level defs."""
    names: dict[str, str] = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    names[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            for alias in node.names:
                names[alias.asname or alias.name] = f"{base}.{alias.name}"
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.setdefault(node.name, f"{module}.{node.name}")
    return names


def _origin(node: ast.AST, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _origin(node.value, names)
        return None if base is None else f"{base}.{node.attr}"
    if isinstance(node, ast.Call):
        base = _origin(node.func, names)
        return None if base is None else f"{base}()"
    return None


def _sites(module: str, source: str, targets: frozenset[str], *, is_package: bool = False) -> set[str]:
    """``module:qualname`` of every function (or ``<module>``) that references
    a target, whether it calls it or aliases it. Importing a PyJWT signer by
    name is itself a site; importing a Bifrost helper by name is not (every
    use of the bound name still is)."""
    tree = ast.parse(source)
    names = _bindings(module, tree, is_package)
    found: set[str] = set()
    scope: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter

        def _record(self) -> None:
            found.add(f"{module}:{'.'.join(scope) or '<module>'}")

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if _origin(node, names) in targets:
                self._record()
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            # A module-level def resolves to itself: only references count.
            if _origin(node, names) in targets:
                self._record()

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            # ``from jwt import encode`` binds a signer even before any use.
            for alias in node.names:
                origin = names.get(alias.asname or alias.name)
                if origin in targets and origin is not None and origin.startswith("jwt."):
                    self._record()

    Visitor().visit(tree)
    return found


def _scan(targets: frozenset[str]) -> set[str]:
    found: set[str] = set()
    for package in _SCANNED:
        for path in sorted((_API_ROOT / package).rglob("*.py")):
            parts = path.relative_to(_API_ROOT).with_suffix("").parts
            is_package = parts[-1] == "__init__"
            module = ".".join(parts[:-1] if is_package else parts)
            found |= _sites(module, path.read_text(), targets, is_package=is_package)
    return found


def test_only_reviewed_functions_sign_jwts() -> None:
    assert _scan(_JWT_SIGNERS) == _JWT_SIGNING_SITES


def test_only_reviewed_functions_mint_access_tokens_outside_the_minter() -> None:
    assert _scan(frozenset({_CREATE_ACCESS_TOKEN})) == _CREATE_ACCESS_TOKEN_SITES


def test_only_the_token_issuers_use_the_run_token_signer() -> None:
    assert _scan(frozenset({_ENCODE_CLAIMS})) == _ENCODE_CLAIMS_SITES


_SIGNER = frozenset(_JWT_SIGNERS)
_MINTER = frozenset({_CREATE_ACCESS_TOKEN})
_RUN_SIGNER = frozenset({_ENCODE_CLAIMS})


def test_each_known_bypass_is_flagged() -> None:
    cases = {
        "import jwt as signing\ndef f(c):\n    return signing.encode(c, 'k')\n": (_SIGNER, "m:f"),
        "from jwt import encode\ndef f(c):\n    return encode(c, 'k')\n": (_SIGNER, "m:f"),
        "from jwt import encode as e\ndef f(c):\n    return e(c, 'k')\n": (_SIGNER, "m:f"),
        "import jwt\ndef f(c):\n    return jwt.PyJWT().encode(c, 'k')\n": (_SIGNER, "m:f"),
        "from jwt.api_jwt import encode\ndef f(c):\n    return encode(c, 'k')\n": (_SIGNER, "m:f"),
        "from jwt import encode as e\n": (_SIGNER, "m:<module>"),
        "import jwt\nsign = None\ndef f():\n    global sign\n    sign = jwt.encode\n": (_SIGNER, "m:f"),
        "from src.core.security import create_access_token as cat\nclass M:\n    def mint(self):\n"
        "        return cat({'sub': 'x'})\n": (_MINTER, "m:M.mint"),
        "import src.core.security as sec\ndef f():\n    return sec.create_access_token({})\n": (_MINTER, "m:f"),
        "from src.core import security\ndef f():\n    return security.create_access_token({})\n": (_MINTER, "m:f"),
        "from src.services.authorization.tokens import _encode_claims\ndef f(c):\n    return _encode_claims(c)\n": (
            _RUN_SIGNER,
            "m:f",
        ),
        "from src.services.authorization import tokens\ndef f(c):\n    return tokens._encode_claims(c)\n": (
            _RUN_SIGNER,
            "m:f",
        ),
    }
    for source, (targets, site) in cases.items():
        assert site in _sites("m", source, targets), source


def test_unrelated_encodes_and_relative_imports_resolve_correctly() -> None:
    source = (
        "import base64\nimport json\n"
        "def f(x):\n    return base64.b64encode(json.dumps(x).encode())\n"
    )
    assert _sites("m", source, _SIGNER) == set()
    # A relative import inside the authorization package names tokens._encode_claims.
    relative = "from .tokens import _encode_claims\ndef f(c):\n    return _encode_claims(c)\n"
    assert "src.services.authorization.sneaky:f" in _sites("src.services.authorization.sneaky", relative, _RUN_SIGNER)
