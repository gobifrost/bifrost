"""Every no-op migration must be paired with a forward cleanup migration.

Turning an already-applied migration into a no-op leaves whatever it created
behind on databases that ran it, while fresh databases never get it — a
divergence no fresh-database test can see, and one that later migrations
collide with. A tombstone therefore needs a forward migration that removes
what the original created, registered here.
"""

from __future__ import annotations

import ast
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

DROP_BUILDER_RESIDUE = "20260929_drop_builder_residue"

# {tombstone revision: cleanup revision}. Merge revisions (tuple
# down_revision) are legitimately empty and are not tombstones.
TOMBSTONES: dict[str, str] = {
    "20260725_private_visibility": DROP_BUILDER_RESIDUE,
    "20260725_builder_tables": DROP_BUILDER_RESIDUE,
    "20260725_config_solution_id": DROP_BUILDER_RESIDUE,
    "20260725_build_jobs": DROP_BUILDER_RESIDUE,
    "20260727_agent_bundle_path": DROP_BUILDER_RESIDUE,
    "20260727_build_plane_jobs": DROP_BUILDER_RESIDUE,
    "20260727_durable_deploy_jobs": DROP_BUILDER_RESIDUE,
    "20260727_promotion_pinning": DROP_BUILDER_RESIDUE,
    "20260730_role_auth_scopes": DROP_BUILDER_RESIDUE,
}


def _revisions() -> tuple[set[str], set[str]]:
    """Return (all revision ids, tombstone revision ids)."""
    all_revisions: set[str] = set()
    tombstones: set[str] = set()
    for path in VERSIONS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        revision = None
        down_revision = None
        upgrade = None
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(target, ast.Name) and node.value is not None:
                    if target.id == "revision":
                        revision = ast.literal_eval(node.value)
                    elif target.id == "down_revision":
                        down_revision = ast.literal_eval(node.value)
            elif isinstance(node, ast.FunctionDef) and node.name == "upgrade":
                upgrade = node
        if revision is None:
            continue
        all_revisions.add(revision)
        if upgrade is None or isinstance(down_revision, (tuple, list)):
            continue
        statements = [
            statement
            for statement in upgrade.body
            if not isinstance(statement, ast.Pass)
            and not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
            )
        ]
        if not statements:
            tombstones.add(revision)
    return all_revisions, tombstones


def test_every_tombstone_has_a_registered_cleanup_migration() -> None:
    all_revisions, tombstones = _revisions()

    unregistered = tombstones - TOMBSTONES.keys()
    assert not unregistered, (
        f"Migration(s) {sorted(unregistered)} have an empty upgrade(). Never "
        "turn an applied migration into a no-op without a forward migration "
        "that drops what it created (databases that ran it keep that schema "
        "and later migrations collide with it). Add the cleanup migration, "
        "add a rehearsal test that applies the residue to a disposable "
        "database (see tests/e2e/platform/test_builder_residue_migration_"
        "rehearsal.py), and register {tombstone: cleanup} in TOMBSTONES."
    )

    stale = TOMBSTONES.keys() - tombstones
    assert not stale, (
        f"TOMBSTONES lists {sorted(stale)}, which no longer have an empty "
        "upgrade(); remove them from the registry."
    )

    missing = {
        cleanup for cleanup in TOMBSTONES.values() if cleanup not in all_revisions
    }
    assert not missing, f"Cleanup revision(s) {sorted(missing)} do not exist."
