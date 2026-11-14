"""Prod-image import hygiene: dev-only packages must stay out of the runtime closure.

Issue #1379 split the test/debug packages out of the production install.
``requirements.lock`` is the runtime closure; the dev-only group (pytest*,
debugpy, watchdog) lives in ``requirements-dev.lock`` (compiled with
``pip-compile --extra dev``) and installs in dev/CI images only. These static
cases fail loudly if shipped runtime code reintroduces a dev-only import, or
if the two locks drift apart. They are intentionally fast (AST + text scans,
no subprocess) so they run in the every-PR unit lane; the fresh-interpreter
closure proofs live in ``test_import_hygiene.py`` (marked slow).
"""

import ast
import re
from pathlib import Path

# Top-level modules that exist only in requirements-dev.lock. They are absent
# from the production image, so importing any of them from shipped runtime
# code is an ImportError at deploy time.
DEV_ONLY_MODULES = frozenset(
    {
        "pytest",
        "pytest_asyncio",
        "_pytest",
        "pytest_cov",
        "pytest_env",
        "pytest_timeout",
        "coverage",  # via pytest-cov
        "debugpy",
        "watchdog",  # dev-loop only in-image: `bifrost watch` / `solution dev`
        "pluggy",  # transitive-only (via pytest), never imported directly
        "iniconfig",  # transitive-only (via pytest), never imported directly
    }
)

# Distributions that must live in requirements-dev.lock, never requirements.lock.
DEV_ONLY_DISTRIBUTIONS = frozenset(
    {
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "pytest-env",
        "pytest-timeout",
        "coverage",
        "debugpy",
        "watchdog",
        "pluggy",
        "iniconfig",
    }
)


def _api_tree(name: str) -> Path:
    """Locate a shipped api/ source tree in-container or in a checkout."""
    candidates = [
        Path("/app") / name,
        Path(__file__).resolve().parents[3] / "api" / name,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(name)


def _repo_file(path: str) -> Path:
    """Locate a repository file in-container (test-runner mounts) or in a checkout."""
    candidates = [
        Path("/app") / path,
        Path("/app") / path.removeprefix("api/"),
        Path(__file__).resolve().parents[3] / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(path)


def _walk_shipped_py(tree: Path) -> list[Path]:
    """Shipped .py files, excluding test files (tests legitimately use pytest)."""
    return [
        path
        for path in sorted(tree.rglob("*.py"))
        if "tests" not in path.parts
        and path.name != "conftest.py"
        and not path.name.startswith("test_")
        and not path.name.endswith("_test.py")
    ]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_roots(tree: ast.AST, *, include_nested: bool) -> set[str]:
    """Absolute import roots. `if TYPE_CHECKING:` blocks never execute, so
    they are always skipped; otherwise nested (function-scope) imports count
    only when include_nested is true."""
    roots: set[str] = set()

    def visit(node: ast.AST, *, nested: bool, type_checking: bool) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            type_checking = True
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            nested = True
        if not nested and not type_checking and isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif not nested and not type_checking and isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        for child in ast.iter_child_nodes(node):
            visit(child, nested=nested, type_checking=type_checking)

    for child in ast.iter_child_nodes(tree):
        # Module scope is never nested; function bodies opt in via include_nested.
        if include_nested or not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            visit(child, nested=False, type_checking=False)
    return roots


def _parse_module(path: Path) -> ast.AST:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(f"{path} does not parse: {exc}") from exc


def _check_tree(tree_name: str, *, include_nested: bool) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _walk_shipped_py(_api_tree(tree_name)):
        if tree_name == "bifrost" and "solution_dev" in path.parts:
            # Dev-loop package: imported lazily by dev-loop commands only
            # (see test_solution_dev_stays_lazily_loaded).
            continue
        hit = sorted(_import_roots(_parse_module(path), include_nested=include_nested) & DEV_ONLY_MODULES)
        if hit:
            offenders[str(path)] = hit
    return offenders


def test_server_runtime_imports_no_dev_only_modules_src():
    offenders = _check_tree("src", include_nested=True)
    assert offenders == {}, f"api/src imports dev-only modules (absent in prod): {offenders}"


def test_server_runtime_imports_no_dev_only_modules_shared():
    offenders = _check_tree("shared", include_nested=True)
    assert offenders == {}, f"api/shared imports dev-only modules (absent in prod): {offenders}"


def test_prod_scripts_import_no_dev_only_modules():
    # api/scripts run at image build time (build_cli_artifact) and container
    # init (init_container) in the production image, which installs the
    # runtime lock only.
    offenders = _check_tree("scripts", include_nested=True)
    assert offenders == {}, f"api/scripts imports dev-only modules (absent in prod): {offenders}"


def test_cli_top_level_imports_stay_dev_free():
    # The shipped CLI lazy-loads watchdog inside dev-loop commands; a
    # top-level dev-only import here would break every worker `bifrost <verb>`
    # shell-out in production.
    offenders = _check_tree("bifrost", include_nested=False)
    assert offenders == {}, f"api/bifrost top-level imports dev-only modules: {offenders}"


def test_solution_dev_stays_lazily_loaded():
    """solution_dev/ may use watchdog, but nothing outside it may import it at
    module scope — otherwise `import bifrost.cli` (the worker shell-out path)
    would drag watchdog into production."""
    offenders: list[str] = []
    for path in _walk_shipped_py(_api_tree("bifrost")):
        if "solution_dev" in path.parts:
            continue
        tree = _parse_module(path)
        for child in ast.iter_child_nodes(tree):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                continue
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Import):
                if any(
                    alias.name == "bifrost.solution_dev"
                    or alias.name.startswith("bifrost.solution_dev.")
                    for alias in child.names
                ):
                    offenders.append(str(path))
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                if module == "bifrost.solution_dev" or module.startswith(
                    "bifrost.solution_dev."
                ):
                    offenders.append(str(path))
                elif child.level > 0 and module.split(".")[0] == "solution_dev":
                    offenders.append(str(path))
    assert offenders == [], f"solution_dev imported at module scope: {offenders}"


_LOCK_NAME = re.compile(
    r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?(?:==([^\s\\;]+)|\s*@\s*(\S+))"
)


def _lock_distributions(path: Path) -> dict[str, str]:
    dists: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _LOCK_NAME.match(line)
        if match:
            dists[match.group(1).lower()] = match.group(2) or match.group(3)
    return dists


def test_lock_distributions_includes_pinned_source_archives(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "ordinary==1.2.3\n"
        "cffi @ https://codeload.github.com/python-cffi/cffi/tar.gz/fixed-commit\n",
        encoding="utf-8",
    )
    assert _lock_distributions(lock) == {
        "ordinary": "1.2.3",
        "cffi": "https://codeload.github.com/python-cffi/cffi/tar.gz/fixed-commit",
    }


def test_prod_lock_excludes_dev_only_distributions():
    dists = _lock_distributions(_repo_file("requirements.lock"))
    leaked = sorted(DEV_ONLY_DISTRIBUTIONS & dists.keys())
    assert leaked == [], f"dev-only distributions in prod lock: {leaked}"


def test_dev_lock_contains_full_consistent_closure():
    """requirements-dev.lock must be a superset of requirements.lock with
    identical shared pins — both locks compile from one index snapshot, and
    dev/CI images install the dev lock instead of (not on top of) the prod
    lock, so any drift here is a dev/prod parity break."""
    prod = _lock_distributions(_repo_file("requirements.lock"))
    dev = _lock_distributions(_repo_file("requirements-dev.lock"))
    missing = sorted(DEV_ONLY_DISTRIBUTIONS - dev.keys())
    assert missing == [], f"dev-only distributions missing from dev lock: {missing}"
    assert set(prod) <= set(dev), (
        f"dev lock is not a superset of prod lock: {sorted(set(prod) - set(dev))}"
    )
    drifted = sorted(
        name for name in prod if name in dev and prod[name] != dev[name]
    )
    assert drifted == [], f"shared pins disagree between locks: {drifted}"


def test_pip_installs_stay_hash_pinned():
    """Scorecard Pinned-Dependencies gate: every pip install in the image
    builds and the lock-regen automation must use --require-hashes."""
    files = [
        _repo_file("api/Dockerfile"),
        _repo_file("api/Dockerfile.dev"),
        _repo_file(".github/workflows/ci.yml"),
        _repo_file(".github/workflows/dependabot-lockfile-regen.yml"),
    ]
    unpinned: list[str] = []
    for path in files:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "pip install" in line and "--require-hashes" not in line:
                unpinned.append(f"{path}:{lineno}: {stripped}")
    assert unpinned == [], f"unpinned pip installs: {unpinned}"


def test_prod_lock_retains_ruff_for_git_sync_preflight():
    # Git sync executes Ruff rather than importing it. Import scans alone
    # cannot protect this production validation capability.
    prod = _lock_distributions(_repo_file("requirements.lock"))
    dev = _lock_distributions(_repo_file("requirements-dev.lock"))
    assert "ruff" in prod, "Git sync preflight requires the Ruff executable in production"
    assert prod["ruff"] == dev["ruff"]
