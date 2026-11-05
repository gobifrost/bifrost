"""The sampler can target a command-owned stack before its API is created."""

import hashlib
import unittest
from unittest.mock import patch

from scripts import memory_sampler


class WorktreeContainerTests(unittest.TestCase):
    @patch("scripts.memory_sampler.subprocess.check_output")
    def test_startup_target_requires_only_worktree_identity(self, check_output):
        check_output.return_value = "/tmp/bifrost-worktree\n"
        expected_hash = hashlib.sha256(b"/tmp/bifrost-worktree").hexdigest()[:8]
        self.assertEqual(
            memory_sampler._worktree_container(), f"bifrost-test-{expected_hash}-api-1",
        )
        self.assertEqual(check_output.call_count, 1)
        self.assertEqual(check_output.call_args.args[0][0], "git")

    @patch("scripts.memory_sampler.subprocess.check_output")
    def test_interactive_detection_still_rejects_an_absent_api(self, check_output):
        check_output.side_effect = ["/tmp/bifrost-worktree\n", "another-worktree-api\n"]
        with self.assertRaisesRegex(SystemExit, "No API container found"):
            memory_sampler._detect_container()


if __name__ == "__main__":
    unittest.main()
