from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_attribution  # noqa: E402


def group(root: Path, relative: str, cpu: int, io: int) -> Path:
    path = root / relative
    path.mkdir(parents=True)
    (path / "cpu.stat").write_text(f"usage_usec {cpu}\n", encoding="utf-8")
    (path / "io.stat").write_text(
        f"259:0 rbytes={io} wbytes=0 rios=1 wios=0\n252:0 rbytes={io} wbytes=0 rios=1 wios=0\n",
        encoding="utf-8",
    )
    return path


class AttributionTest(unittest.TestCase):
    def test_creation_deletion_reset_and_disjoint_scopes_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker = group(root, "system.slice/docker-a.scope", 10, 20)
            group(root, "system.slice/docker-a.scope/nested.scope", 99, 99)
            first = host_attribution.snapshot(root)
            self.assertEqual(set(first["groups"]), {"system.slice/docker-a.scope"})
            self.assertIn("observer_cpu_process_s", first)
            self.assertEqual(first["groups"]["system.slice/docker-a.scope"]["io_bytes"], 20)
            self.assertEqual(first["groups"]["system.slice/docker-a.scope"]["io_ios"], 1)
            (docker / "cpu.stat").write_text("usage_usec 15\n", encoding="utf-8")
            stable = host_attribution.delta(first, host_attribution.snapshot(root))
            self.assertEqual(stable["deltas"]["system.slice/docker-a.scope"]["cpu_usec"], 5)
            group(
                root,
                r"machine.slice/machine-qemu\x2d12\x2dbifrost\x2dlab.scope",
                5,
                8,
            )
            (docker / "cpu.stat").write_text("usage_usec 3\n", encoding="utf-8")
            second = host_attribution.snapshot(root)
            measured = host_attribution.delta(first, second)
            self.assertIn(
                r"machine.slice/machine-qemu\x2d12\x2dbifrost\x2dlab.scope",
                measured["coverage"]["new"],
            )
            self.assertIn("system.slice/docker-a.scope", measured["coverage"]["counter_reset"])
            self.assertEqual(measured["deltas"], {})
            shutil.rmtree(docker)
            third = host_attribution.snapshot(root)
            self.assertIn("system.slice/docker-a.scope", host_attribution.delta(second, third)["coverage"]["deleted"])

    def test_missing_io_counter_is_not_reported_as_a_deleted_cgroup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = group(root, "system.slice/docker-a.scope", 10, 20)
            first = host_attribution.snapshot(root)
            (path / "io.stat").unlink()
            second = host_attribution.snapshot(root)
            coverage = host_attribution.delta(first, second)["coverage"]
            self.assertEqual(coverage["missing"], ["system.slice/docker-a.scope"])
            self.assertEqual(coverage["deleted"], [])

    def test_identity_change_is_not_subtracted_as_a_stable_cgroup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker = group(root, "system.slice/docker-a.scope", 10, 20)
            first = host_attribution.snapshot(root)
            shutil.rmtree(docker)
            group(root, "system.slice/docker-a.scope", 4, 8)
            second = host_attribution.snapshot(root)
            second["groups"]["system.slice/docker-a.scope"]["inode"] = (
                first["groups"]["system.slice/docker-a.scope"]["inode"] + 1
            )
            measured = host_attribution.delta(first, second)
            self.assertIn("system.slice/docker-a.scope", measured["coverage"]["identity_changed"])
            self.assertEqual(measured["deltas"], {})

