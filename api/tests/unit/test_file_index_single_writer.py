"""file_index / solution_file_index have exactly one writer: FileIndexService."""

import re
from pathlib import Path

API = Path(__file__).resolve().parents[2]
WRITE = re.compile(r"\b(insert|delete|update)\(\s*(FileIndex|SolutionFileIndex)\b")
ALLOWED = {
    "src/services/file_index_service.py",
    "src/services/file_index_reconciler.py",  # rewritten onto FileIndexService in Task 3
}


def test_only_file_index_service_writes_index_tables():
    offenders = []
    for root in ("src", "shared"):
        for path in (API / root).rglob("*.py"):
            rel = path.relative_to(API).as_posix()
            if rel not in ALLOWED and WRITE.search(path.read_text(encoding="utf-8")):
                offenders.append(rel)
    assert sorted(offenders) == [], f"route index writes through FileIndexService: {offenders}"
