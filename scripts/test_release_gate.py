import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import release_gate


SHA = "a" * 40
ENTRY = "11111111-1111-4111-8111-111111111111"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class ReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "entries").mkdir()
        shutil.copy(
            Path(__file__).resolve().parents[1] / "product-updates" / "schema.json",
            self.root / "schema.json",
        )
        write_json(
            self.root / "inventory.json",
            {
                "base_ref": "b" * 40,
                "target_ref": SHA,
                "commits": [{"sha": SHA, "prs": [1]}],
                "prs": [{"number": 1, "merge_commit": SHA}],
            },
        )
        write_json(self.root / "dispositions.json", {})
        self.disposition = self.root / "dispositions" / "pr-1.json"
        self.disposition.parent.mkdir()
        write_json(
            self.disposition,
            {
                "key": "pr:1",
                "classification": "highlight",
                "entry_ids": [ENTRY],
                "review": {"status": "approved"},
                "security_review": {"status": "required"},
                "action_required": True,
            },
        )
        (self.root / "entries" / f"{ENTRY}.md").write_text(
            "---json\n"
            + json.dumps(
                {
                    "id": ENTRY,
                    "review": {"status": "approved"},
                    "security_review": {"status": "required"},
                    "action_required": True,
                    "sources": [{"pr": 1}],
                    "eligibility": {"requires_prs": [1], "requires_commits": [SHA]},
                }
            )
            + "\n---\nText\n"
        )
        write_json(
            self.root / "release-review.json",
            {
                "target_ref": SHA,
                "review": {"status": "approved", "evidence": ["review"]},
                "security": {
                    "status": "approved",
                    "evidence": ["advisory"],
                    "fixed_cves": ["None in this release"],
                },
                "breaking_changes": {
                    "status": "approved",
                    "evidence": ["migration"],
                    "summary": "None in this release",
                },
            },
        )

    def test_dependency_advisory_omission_still_requires_a_release_notice(self) -> None:
        item = json.loads(self.disposition.read_text())
        item["classification"] = "omit"
        item["entry_ids"] = []
        item.pop("action_required")
        item["security_review"]["review_ref"] = "https://github.com/gobifrost/bifrost/pull/1"
        write_json(self.disposition, item)
        result = release_gate.validate(self.root, SHA)
        self.assertIn(
            "pr:1: security or action-required source must use a canonical highlight entry",
            result.errors,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_reviewed_coverage_emits_required_release_material(self) -> None:
        result = release_gate.validate(self.root, SHA)

        self.assertEqual([], result.errors)
        self.assertIn("## Fixed CVEs", result.markdown)
        self.assertIn("## Breaking Changes", result.markdown)

    def test_release_subrange_does_not_require_an_older_notice_again(self) -> None:
        (self.root / "entries" / f"{ENTRY}.md").unlink()
        result = release_gate.validate(self.root, SHA, base=SHA)
        self.assertEqual([], result.errors)

    def test_draft_disposition_blocks_a_release(self) -> None:
        item = json.loads(self.disposition.read_text())
        item["review"]["status"] = "draft"
        write_json(self.disposition, item)

        result = release_gate.validate(self.root, SHA)

        self.assertIn(
            "pr:1: release disposition review must be approved", result.errors
        )

    def test_pending_group_cannot_suppress_required_notice(self) -> None:
        path = self.root / "entries" / f"{ENTRY}.md"
        path.write_text(
            path.read_text().replace('"requires_prs": [1]', '"requires_prs": [1, 2]')
        )
        result = release_gate.validate(self.root, SHA)
        self.assertTrue(
            any("canonical security-reviewed entry" in error for error in result.errors)
        )
        self.assertTrue(
            any("canonical action-required entry" in error for error in result.errors)
        )

    def test_security_or_action_required_source_cannot_be_hidden_as_other(self) -> None:
        item = json.loads(self.disposition.read_text())
        item["classification"] = "other"
        item["entry_ids"] = []
        write_json(self.disposition, item)

        result = release_gate.validate(self.root, SHA)

        self.assertIn(
            "pr:1: security or action-required source must use a canonical highlight entry",
            result.errors,
        )


if __name__ == "__main__":
    unittest.main()
