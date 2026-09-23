"""Tests for the local CodeQL PR line filter."""

import unittest

from scripts.codeql_changed_lines import changed_lines


class ChangedLinesTests(unittest.TestCase):
    def test_tracks_additions_and_skips_deletions(self) -> None:
        diff = """diff --git a/api/first.py b/api/first.py
--- a/api/first.py
+++ b/api/first.py
@@ -4,0 +5,2 @@
+one
+two
@@ -20,1 +22,0 @@
-gone
diff --git a/api/second.py b/api/second.py
--- /dev/null
+++ b/api/second.py
@@ -0,0 +1,3 @@
+a
+b
+c
"""

        self.assertEqual(changed_lines(diff), {
            "api/first.py": [[5, 6]],
            "api/second.py": [[1, 3]],
        })
