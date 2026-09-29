"""The migration graph must have exactly one head.

A second head only surfaces when the init container runs ``upgrade head``
during a deploy, so catch it here instead.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


def test_alembic_has_a_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, (
        f"Alembic has {len(heads)} heads: {sorted(heads)}. Re-point your new "
        "migration's down_revision at the current head (or add a merge "
        "revision) so the graph has exactly one head."
    )
