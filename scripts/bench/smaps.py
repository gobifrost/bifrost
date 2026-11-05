"""Classify a process's /proc/<pid>/smaps into allocator regions.

Reads smaps text from outside the process, so it works against any build
(old releases included) and in production through ``kubectl exec ... cat``,
with no in-process probe and no Python started next to the target.

Regions:
- ``heap``: glibc's main arena (the ``[heap]`` brk segment). Its virtual size
  is the main arena's high-water mark; it only shrinks from the top.
- ``anon_large``: anonymous mappings of 1 MiB or more. Adjacent pymalloc
  arenas (1 MiB each) merge into one mapping, so this holds pymalloc arenas,
  glibc's secondary arenas, and large ``mmap``-served allocations.
- ``anon_small``: anonymous mappings under 1 MiB.
- ``file``: file-backed mappings (interpreter, shared libraries).
- ``other``: ``[stack]``, ``[vdso]`` and similar.

``thp`` is the AnonHugePages total: resident memory backed by 2 MiB
transparent huge pages, which stay fully resident while any byte in them is
in use.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_HEADER = re.compile(r"^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$")
_KIB = 1024
_ONE_MIB_KIB = 1024


@dataclass
class Region:
    mappings: int = 0
    virtual_kib: int = 0
    rss_kib: int = 0
    thp_kib: int = 0


@dataclass
class SmapsSummary:
    rss_kib: int = 0
    pss_kib: int = 0
    anonymous_kib: int = 0
    thp_kib: int = 0
    regions: dict[str, Region] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _region_name(path: str, size_kib: int, anonymous_kib: int) -> str:
    if path == "[heap]":
        return "heap"
    if path.startswith("/"):
        return "file"
    if path.startswith("["):
        return "other"
    if anonymous_kib == 0:
        return "anon_untouched"
    return "anon_large" if size_kib >= _ONE_MIB_KIB else "anon_small"


def summarize(smaps_text: str) -> SmapsSummary:
    summary = SmapsSummary()
    current: dict | None = None
    mappings: list[dict] = []
    for line in smaps_text.splitlines():
        header = _HEADER.match(line)
        if header:
            start, end = int(header.group(1), 16), int(header.group(2), 16)
            current = {
                "size": (end - start) // _KIB,
                "path": header.group(4).strip(),
                "Rss": 0,
                "Pss": 0,
                "Anonymous": 0,
                "AnonHugePages": 0,
            }
            mappings.append(current)
            continue
        key, _, value = line.partition(":")
        if current is not None and key in current and key not in ("size", "path"):
            current[key] = int(value.split()[0])

    for mapping in mappings:
        name = _region_name(mapping["path"], mapping["size"], mapping["Anonymous"])
        region = summary.regions.setdefault(name, Region())
        region.mappings += 1
        region.virtual_kib += mapping["size"]
        region.rss_kib += mapping["Rss"]
        region.thp_kib += mapping["AnonHugePages"]
        summary.rss_kib += mapping["Rss"]
        summary.pss_kib += mapping["Pss"]
        summary.anonymous_kib += mapping["Anonymous"]
        summary.thp_kib += mapping["AnonHugePages"]
    return summary


def format_table(summary: SmapsSummary) -> str:
    rows = [f"{'region':16} {'maps':>5} {'virt MiB':>9} {'RSS MiB':>8} {'THP MiB':>8}"]
    for name, region in sorted(summary.regions.items(), key=lambda item: -item[1].rss_kib):
        rows.append(
            f"{name:16} {region.mappings:5} {region.virtual_kib / 1024:9.1f} "
            f"{region.rss_kib / 1024:8.1f} {region.thp_kib / 1024:8.1f}"
        )
    rows.append(
        f"{'total':16} {'':5} {'':9} {summary.rss_kib / 1024:8.1f} {summary.thp_kib / 1024:8.1f}"
        f"   (PSS {summary.pss_kib / 1024:.1f} MiB)"
    )
    return "\n".join(rows)


if __name__ == "__main__":
    import sys

    with open(sys.argv[1], encoding="utf-8") as smaps_file:
        print(format_table(summarize(smaps_file.read())))
