from __future__ import annotations

import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.services.builder.revision_inspection import (
    diff_revisions,
    list_revision_files,
    read_revision_file,
)


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.fixture
def revisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first = uuid4()
    second = uuid4()
    archives: dict[UUID, Path] = {}
    for revision_id, files in [
        (
            first,
            {
                "apps/demo/src/App.tsx": b"export function App() { return null; }\n",
                "assets/logo.png": b"\x89PNG\x00",
            },
        ),
        (
            second,
            {
                "apps/demo/src/App.tsx": b"export function App() { return <main />; }\n",
                "README.md": b"# Demo\n",
                "assets/logo.png": b"\x89PNG\x00",
            },
        ),
    ]:
        archive = tmp_path / f"{revision_id}.zip"
        _write_zip(archive, files)
        archives[revision_id] = archive

    async def copy_to_path(_storage, revision_id, destination: Path) -> bool:
        source = archives.get(UUID(str(revision_id)))
        if source is None:
            return False
        destination.write_bytes(source.read_bytes())
        return True

    monkeypatch.setattr(
        "src.services.builder.revision_inspection.SolutionRevisionStorage.copy_to_path",
        copy_to_path,
    )
    return first, second


@pytest.mark.asyncio
async def test_lists_and_reads_revision_files(revisions) -> None:
    first, _second = revisions
    solution_id = uuid4()

    listed = await list_revision_files(solution_id, first)
    assert [file.path for file in listed.files] == [
        "apps/demo/src/App.tsx",
        "assets/logo.png",
    ]
    assert listed.files[0].is_text is True
    assert listed.files[1].is_text is False

    content = await read_revision_file(
        solution_id, first, "apps/demo/src/App.tsx"
    )
    assert content.encoding == "utf-8"
    assert content.content == "export function App() { return null; }\n"


@pytest.mark.asyncio
async def test_diff_reports_added_and_modified_files(revisions) -> None:
    first, second = revisions

    result = await diff_revisions(uuid4(), second, first)

    assert result.total == 2
    by_path = {file.path: file for file in result.files}
    assert by_path["README.md"].status == "added"
    assert by_path["apps/demo/src/App.tsx"].status == "modified"
    assert "+export function App() { return <main />; }" in (
        by_path["apps/demo/src/App.tsx"].diff or ""
    )
    assert "assets/logo.png" not in by_path
