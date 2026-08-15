"""Solution-level icon: ``logo:`` in bifrost.solution.yaml → deploy → install.

``_decode_logo`` is the shared validator (apps + the solution icon): content
type allow-list, 5 MB cap, SVG sanitization, and ``None`` when no logo is
declared. Deploy stamps the decoded icon on the install — deploy-owned, so a
bundle WITHOUT a logo clears any prior one.
"""
from __future__ import annotations

import base64
import io
from uuid import uuid4

import pytest
from PIL import Image

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    SolutionDeployConflict,
    _decode_logo,
)

_png_buffer = io.BytesIO()
Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(_png_buffer, "PNG")
PNG = _png_buffer.getvalue()
PNG_B64 = base64.b64encode(PNG).decode("ascii")


class TestDecodeLogo:
    def test_absent_logo_decodes_to_none(self) -> None:
        assert _decode_logo("solution 'x'", None, None) is None
        assert _decode_logo("solution 'x'", "", "image/png") is None

    def test_png_roundtrip(self) -> None:
        processed = _decode_logo("solution 'x'", PNG_B64, "image/png")
        assert processed is not None
        assert processed.original_data == PNG
        assert processed.original_content_type == "image/png"
        assert processed.thumbnail_content_type == "image/webp"
        assert len(processed.thumbnail_data) <= 20 * 1024

    def test_disallowed_content_type_raises_with_label(self) -> None:
        with pytest.raises(SolutionDeployConflict) as exc:
            _decode_logo("solution 'x'", PNG_B64, "image/gif")
        assert "solution 'x'" in str(exc.value)

    def test_oversized_logo_raises(self) -> None:
        big = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode("ascii")
        with pytest.raises(SolutionDeployConflict) as exc:
            _decode_logo("solution 'x'", big, "image/png")
        assert "too large" in str(exc.value).lower()

    def test_svg_is_sanitized(self) -> None:
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect/></svg>'
        processed = _decode_logo(
            "solution 'x'", base64.b64encode(svg).decode("ascii"), "image/svg+xml"
        )
        assert processed is not None
        assert processed.original_content_type == "image/svg+xml"
        assert b"<script" not in processed.original_data
        assert processed.thumbnail_content_type == "image/webp"


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """See test_solution_deploy_reconcile: drop the loop-bound Redis singleton
    so cross-test event-loop teardown can't poison the deployer's cache sync."""
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestSolutionLogoDeploy:
    async def _make_install(self, db, slug: str) -> Solution:
        sol = Solution(id=uuid4(), slug=slug, name=slug.upper(), organization_id=None)
        db.add(sol)
        await db.flush()
        return sol

    def _bundle(
        self,
        sol: Solution,
        logo_b64: str | None,
        logo_content_type: str | None,
    ) -> SolutionBundle:
        return SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "def run():\n    return 1\n"},
            workflows=[
                {
                    "id": str(uuid4()),
                    "name": "w1",
                    "function_name": "run",
                    "path": "workflows/w1.py",
                    "type": "workflow",
                }
            ],
            logo_b64=logo_b64,
            logo_content_type=logo_content_type,
        )

    async def test_deploy_stamps_solution_logo(self, db_session) -> None:
        db = db_session
        sol = await self._make_install(db, f"logo-{uuid4().hex[:8]}")
        await SolutionDeployer(db).deploy(self._bundle(sol, PNG_B64, "image/png"))
        await db.flush()
        assert sol.logo_data == PNG
        assert sol.logo_content_type == "image/png"
        assert sol.logo_thumbnail_data is not None
        assert sol.logo_thumbnail_content_type == "image/webp"
        assert sol.logo_thumbnail_version is not None

    async def test_logoless_bundle_clears_prior_logo(self, db_session) -> None:
        """Deploy is the publish: a logo dropped from the manifest is dropped
        from the install."""
        db = db_session
        sol = await self._make_install(db, f"logo-{uuid4().hex[:8]}")
        sol.logo_data = PNG
        sol.logo_content_type = "image/png"
        await db.flush()
        await SolutionDeployer(db).deploy(self._bundle(sol, None, None))
        await db.flush()
        assert sol.logo_data is None
        assert sol.logo_content_type is None
        assert sol.logo_thumbnail_data is None
        assert sol.logo_thumbnail_content_type is None
        assert sol.logo_thumbnail_version is None

    async def test_invalid_logo_fails_the_deploy(self, db_session) -> None:
        db = db_session
        sol = await self._make_install(db, f"logo-{uuid4().hex[:8]}")
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(
                self._bundle(sol, PNG_B64, "image/gif")
            )
