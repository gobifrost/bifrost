"""Regression coverage for source-driven E2E shard allocation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import e2e_shard


class E2EShardTests(unittest.TestCase):
    def test_large_files_are_separated_without_a_timing_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lengths = {
                "tests/e2e/test_a_large.py": 100,
                "tests/e2e/test_b_small.py": 1,
                "tests/e2e/test_c_large.py": 100,
                "tests/e2e/test_d_small.py": 1,
            }
            for name, length in lengths.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * length)

            files = list(lengths)
            with patch.object(e2e_shard, "API_ROOT", root):
                shards = e2e_shard.split(files, 2)
                assert shards == e2e_shard.split(list(reversed(files)), 2)

            self.assertEqual([101, 101], [sum(lengths[f] for f in shard) for shard in shards])
            self.assertCountEqual(files, [file for shard in shards for file in shard])

    def test_three_shards_cover_every_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [f"tests/e2e/test_{index}.py" for index in range(9)]
            for index, name in enumerate(files):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * (index + 1))

            with patch.object(e2e_shard, "API_ROOT", root):
                shards = e2e_shard.split(files, 3)
                self.assertEqual(shards, e2e_shard.split(list(reversed(files)), 3))

            self.assertEqual(3, len(shards))
            self.assertTrue(all(shards))
            self.assertCountEqual(files, [file for shard in shards for file in shard])


if __name__ == "__main__":
    unittest.main()
