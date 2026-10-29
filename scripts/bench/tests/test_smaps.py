from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smaps  # noqa: E402


def _mapping(start: int, size_kib: int, path: str, rss: int, anonymous: int, thp: int = 0) -> str:
    end = start + size_kib * 1024
    return (
        f"{start:x}-{end:x} rw-p 00000000 00:00 0    {path}\n"
        f"Size:           {size_kib} kB\n"
        f"Rss:            {rss} kB\n"
        f"Pss:            {rss} kB\n"
        f"Anonymous:      {anonymous} kB\n"
        f"AnonHugePages:  {thp} kB\n"
    )


FIXTURE = "".join(
    [
        _mapping(0x1000000, 2048, "[heap]", rss=1000, anonymous=1000, thp=512),
        _mapping(0x2000000, 1024, "", rss=1024, anonymous=1024),
        _mapping(0x2100000, 1024, "", rss=900, anonymous=900),
        _mapping(0x3000000, 64, "", rss=60, anonymous=60),
        _mapping(0x4000000, 512, "/usr/lib/x.so", rss=300, anonymous=0),
        _mapping(0x5000000, 8192, "", rss=0, anonymous=0),
    ]
)


class SummarizeTest(unittest.TestCase):
    def test_regions_and_totals(self) -> None:
        summary = smaps.summarize(FIXTURE)
        regions = summary.regions
        self.assertEqual(regions["heap"].virtual_kib, 2048)
        self.assertEqual(regions["heap"].rss_kib, 1000)
        self.assertEqual(regions["heap"].thp_kib, 512)
        self.assertEqual(regions["anon_large"].mappings, 2)
        self.assertEqual(regions["anon_large"].rss_kib, 1924)
        self.assertEqual(regions["anon_small"].rss_kib, 60)
        self.assertEqual(regions["file"].rss_kib, 300)
        self.assertEqual(regions["anon_untouched"].virtual_kib, 8192)
        self.assertEqual(summary.rss_kib, 1000 + 1924 + 60 + 300)
        self.assertEqual(summary.anonymous_kib, 1000 + 1924 + 60)
        self.assertEqual(summary.thp_kib, 512)

    def test_empty_input(self) -> None:
        summary = smaps.summarize("")
        self.assertEqual(summary.rss_kib, 0)
        self.assertEqual(summary.regions, {})


if __name__ == "__main__":
    unittest.main()
