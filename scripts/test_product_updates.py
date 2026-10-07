"""Contract tests for the deterministic product-update toolchain."""

import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import product_updates
from scripts.prepare_product_updates_image import prepare


BASE = "a" * 40
TARGET = "b" * 40
COMMIT = "c" * 40
ENTRY_ID = "11111111-1111-4111-8111-111111111111"


class ProductUpdatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.content = self.root / "product-updates"
        (self.content / "entries").mkdir(parents=True)
        shutil.copyfile(
            Path(__file__).parent.parent / "product-updates" / "schema.json",
            self.content / "schema.json",
        )
        (self.content / "assets" / ENTRY_ID).mkdir(parents=True)
        (self.content / "assets" / ENTRY_ID / "screen.png").write_bytes(b"png")
        self.inventory = self.content / "inventory.json"
        self.dispositions = self.content / "dispositions.json"
        self._write_json(self.inventory, self.inventory_data())
        self._write_json(self.dispositions, self.disposition_data())
        self.write_entry()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def inventory_data(self) -> dict:
        return {
            "schema_version": 1,
            "repository": "gobifrost/bifrost",
            "base_ref": BASE,
            "target_ref": TARGET,
            "collected_at": "2026-10-06T12:00:00Z",
            "source_ref": "https://api.github.com/repos/gobifrost/bifrost/compare/base...target",
            "commits": [{"sha": COMMIT, "subject": "Useful change", "prs": [10]}],
            "prs": [
                {
                    "number": 10,
                    "title": "Useful change",
                    "url": "https://github.com/gobifrost/bifrost/pull/10",
                    "author": {"login": "octocat", "type": "User"},
                    "merged_at": "2026-10-05T12:00:00Z",
                    "merge_commit": COMMIT,
                    "metadata_ref": "https://api.github.com/repos/gobifrost/bifrost/pulls/10",
                    "contributions": [
                        {
                            "login": "helper",
                            "profile_url": "https://github.com/helper",
                            "source_pr": 10,
                            "role": "original implementation",
                            "evidence_ref": "https://github.com/gobifrost/bifrost/pull/9",
                        }
                    ],
                }
            ],
        }

    def disposition_data(self) -> dict:
        return {
            "schema_version": 1,
            "inventory": {"base_ref": BASE, "target_ref": TARGET},
            "items": {
                "pr:10": {
                    "classification": "highlight",
                    "entry_ids": [ENTRY_ID],
                    "review": {"status": "approved", "evidence": ["product review"]},
                    "security_review": {"status": "not_required"},
                }
            },
        }

    def entry_data(self) -> dict:
        return {
            "id": ENTRY_ID,
            "revision": 1,
            "published_at": "2026-10-06T12:00:00Z",
            "title": "Useful Change",
            "visibility": "highlight",
            "area": "Platform",
            "type": "Improved",
            "action_required": False,
            "security_review": {"status": "not_required"},
            "sources": [{"pr": 10, "commit": COMMIT}],
            "eligibility": {"requires_prs": [10], "requires_commits": [COMMIT]},
            "assets": [
                {
                    "path": f"assets/{ENTRY_ID}/screen.png",
                    "alt": "The useful setting",
                    "caption": "A setting",
                }
            ],
            "review": {"status": "approved", "evidence": ["product review"]},
        }

    def write_entry(
        self, data: dict | None = None, body: str = "Approved **Markdown**."
    ) -> None:
        data = data or self.entry_data()
        (self.content / "entries" / f"{data['id']}.md").write_text(
            f"---json\n{json.dumps(data, indent=2)}\n---\n{body}\n", encoding="utf-8"
        )

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_bundle_is_stable_eligible_and_uses_cached_verified_credits(self) -> None:
        first = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        second = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )

        self.assertEqual(first, second)
        self.assertEqual([ENTRY_ID], [entry["id"] for entry in first["entries"]])
        self.assertEqual(
            "/product-updates/assets/11111111-1111-4111-8111-111111111111/screen.png",
            first["entries"][0]["assets"][0]["url"],
        )
        self.assertEqual(
            {"octocat", "helper"},
            {c["login"] for c in first["entries"][0]["contributors"]},
        )

    def test_image_history_retains_entries_excluded_from_a_later_release(self) -> None:
        (self.root / "api").mkdir()
        history = prepare(self.root, TARGET)
        release = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET,
            "/product-updates/", base=COMMIT,
        )
        self.assertEqual([ENTRY_ID], [entry["id"] for entry in history["entries"]])
        self.assertEqual([], release["entries"])
        self.assertEqual(
            b"png",
            (self.root / "client/public/product-updates/assets" / ENTRY_ID / "screen.png").read_bytes(),
        )
        self.assertEqual(history, json.loads((self.root / "api/product-updates.bundle.json").read_text()))

    def test_coverage_reconciles_every_cached_landed_item_once(self) -> None:
        report = product_updates.coverage_report(
            product_updates.read_json(self.inventory),
            product_updates.read_json(self.dispositions),
            [self.entry_data()],
        )

        self.assertEqual([], report["unclassified"])
        self.assertEqual([], report["unrepresented_highlights"])
        self.assertEqual([], report["uncovered_commits"])

    def test_build_target_may_be_a_real_git_descendant_of_the_frozen_range(
        self,
    ) -> None:
        repository = self.root / "git"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        refs = []
        for subject in ("base", "landed", "later build"):
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "user.name=Test",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    subject,
                ],
                cwd=repository,
                check=True,
            )
            refs.append(
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=repository, text=True
                ).strip()
            )
        inventory = self.inventory_data()
        inventory.update(base_ref=refs[0], target_ref=refs[1])
        inventory["commits"][0]["sha"] = refs[1]
        inventory["prs"][0]["merge_commit"] = refs[1]
        self._write_json(self.inventory, inventory)
        disposition = self.disposition_data()
        disposition["inventory"] = {"base_ref": refs[0], "target_ref": refs[1]}
        self._write_json(self.dispositions, disposition)
        entry = self.entry_data()
        entry["sources"][0]["commit"] = refs[1]
        entry["eligibility"]["requires_commits"] = [refs[1]]
        self.write_entry(entry)
        previous_directory = Path.cwd()
        try:
            os.chdir(repository)
            self.assertEqual(
                [],
                product_updates.validate_content(
                    self.content, self.inventory, self.dispositions, refs[2]
                ),
            )
            errors = product_updates.validate_content(
                self.content, self.inventory, self.dispositions, "f" * 40
            )
            self.assertTrue(any("verified descendant" in error for error in errors))
        finally:
            os.chdir(previous_directory)

    def test_each_associated_pr_needs_its_own_disposition(self) -> None:
        inventory = self.inventory_data()
        inventory["commits"][0]["prs"].append(11)
        report = product_updates.coverage_report(
            inventory, self.disposition_data(), [self.entry_data()]
        )
        self.assertEqual(["pr:11"], report["unclassified"])
        self.assertEqual([COMMIT], report["uncovered_commits"])

    def test_duplicate_entry_identity_is_rejected(self) -> None:
        source = self.content / "entries" / f"{ENTRY_ID}.md"
        shutil.copyfile(source, source.with_name("duplicate.md"))
        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )
        self.assertTrue(any("duplicate entry id" in error for error in errors))

    def test_external_schema_references_cannot_trigger_network_resolution(self) -> None:
        path = self.content / "schema.json"
        schema = json.loads(path.read_text())
        schema["$defs"]["entry"]["properties"]["title"] = {
            "$ref": "https://example.com/untrusted-schema"
        }
        self._write_json(path, schema)
        errors = product_updates.schema_errors(
            path, "entry", self.entry_data(), "entry"
        )
        self.assertTrue(any("external $ref" in error for error in errors))

    def test_requires_all_sources_and_approved_review(self) -> None:
        data = self.entry_data()
        data["eligibility"]["requires_prs"].append(11)
        (self.content / "entries" / f"{ENTRY_ID}.md").unlink()
        self.write_entry(data)

        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )

        self.assertEqual([], errors)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertEqual([], bundle["entries"])
        self.assertEqual(
            ["pr:10"], [item["source"] for item in bundle["other_changes"]]
        )

    def test_uncached_cited_source_is_withheld_even_without_declared_prerequisite(
        self,
    ) -> None:
        data = self.entry_data()
        data["sources"].append({"pr": 11})
        self.write_entry(data)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertEqual([], bundle["entries"])
        self.assertEqual(1, len(bundle["other_changes"]))

    def test_canonical_other_disposition_prevents_duplicate_highlight(self) -> None:
        data = self.disposition_data()
        data["items"]["pr:10"].update(
            classification="other", entry_ids=[], summary="Reviewed smaller change"
        )
        self._write_json(self.dispositions, data)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertEqual([], bundle["entries"])
        self.assertEqual("Reviewed smaller change", bundle["other_changes"][0]["title"])

    def test_draft_source_review_withholds_approved_entry(self) -> None:
        data = self.disposition_data()
        data["items"]["pr:10"]["review"]["status"] = "draft"
        self._write_json(self.dispositions, data)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertEqual([], bundle["entries"])
        self.assertEqual([], bundle["other_changes"])

    def test_rejects_verbose_notes(self) -> None:
        self.write_entry(body="word " * 61)
        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )
        self.assertTrue(any("exceeds 60 words" in error for error in errors))

    def test_release_only_notice_remains_in_release_material(self) -> None:
        data = self.entry_data()
        data["in_app"] = False
        self.write_entry(data, "A required release notice.")
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertFalse(bundle["entries"][0]["in_app"])
        rendered = product_updates.render_release(
            bundle, f"https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/"
        )
        self.assertIn("A required release notice.", rendered)

    def test_release_uses_app_link_labels_without_inventing_a_deployment_url(
        self,
    ) -> None:
        self.write_entry(body="See effective access. [Open Users](/users)")
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        rendered = product_updates.render_release(
            bundle, f"https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/"
        )
        self.assertIn("**Open Users** (in Bifrost)", rendered)
        self.assertNotIn("](/users)", rendered)

    def test_rejects_unsafe_assets_and_unreviewed_security_updates(self) -> None:
        data = self.entry_data()
        data["assets"][0]["path"] = "../escape.svg"
        data["type"] = "Security"
        data["security_review"] = {"status": "not_required"}
        (self.content / "entries" / f"{ENTRY_ID}.md").unlink()
        self.write_entry(data, "<script>alert(1)</script>")

        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )

        self.assertTrue(any("unsafe asset path" in error for error in errors))
        self.assertTrue(
            any("Security entries require explicit review" in error for error in errors)
        )
        self.assertTrue(any("raw HTML" in error for error in errors))

    def test_release_render_uses_pinned_assets_and_excludes_owner_and_bots_from_rollup(
        self,
    ) -> None:
        inventory = self.inventory_data()
        inventory["prs"][0]["author"] = {"login": "jackmusick", "type": "User"}
        inventory["prs"][0]["contributions"].append(
            {
                "login": "dependabot[bot]",
                "profile_url": "https://github.com/apps/dependabot",
                "source_pr": 10,
                "evidence_ref": "https://github.com/gobifrost/bifrost/pull/10",
            }
        )
        self._write_json(self.inventory, inventory)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )

        body = product_updates.render_release(
            bundle, f"https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/"
        )

        self.assertIn(
            f"https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/product-updates/assets/",
            body,
        )
        self.assertNotIn("](/product-updates/assets/", body)
        self.assertIn("[helper](https://github.com/helper)", body)
        self.assertNotIn("jackmusick", body)
        self.assertNotIn("dependabot", body)

    def test_markdown_cannot_link_an_undeclared_image(self) -> None:
        self.write_entry(body="![Untracked image](../private.png)")
        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )
        self.assertTrue(any("declared local asset" in error for error in errors))

    def test_event_scope_requires_a_canonical_disposition_for_each_pr(self) -> None:
        errors = product_updates.validate_event(
            {"pull_request": {"number": 10}},
            product_updates.read_json(self.dispositions),
        )
        self.assertEqual([], errors)
        errors = product_updates.validate_event(
            {"merge_group": {"pull_requests": [{"number": 10}, {"number": 11}]}},
            product_updates.read_json(self.dispositions),
        )
        self.assertEqual(["missing canonical disposition for pr:11"], errors)

    def test_event_accepts_active_premerge_highlight_with_verified_author(self) -> None:
        entry = self.entry_data()
        entry["contributors"] = [
            {
                "login": "octocat",
                "profile_url": "https://github.com/octocat",
                "source_pr": 10,
            }
        ]
        errors = product_updates.validate_event(
            {"pull_request": {"number": 10, "user": {"login": "octocat"}}},
            product_updates.read_json(self.dispositions),
            [entry],
        )
        self.assertEqual([], errors)
        errors = product_updates.validate_event(
            {"pull_request": {"number": 10, "user": {"login": "wrong"}}},
            product_updates.read_json(self.dispositions),
            [entry],
        )
        self.assertTrue(any("verified event author" in error for error in errors))

    def test_merge_group_rejects_draft_highlight_even_with_approved_disposition(
        self,
    ) -> None:
        entry = self.entry_data()
        entry["review"]["status"] = "draft"
        entry["contributors"] = [
            {
                "login": "octocat",
                "profile_url": "https://github.com/octocat",
                "source_pr": 10,
            }
        ]
        errors = product_updates.validate_event(
            {
                "merge_group": {
                    "pull_requests": [{"number": 10, "user": {"login": "octocat"}}]
                }
            },
            self.disposition_data(),
            [entry],
        )
        self.assertTrue(any("draft review" in error for error in errors))

    def test_typescript_resolves_bundle_schema_references(self) -> None:
        generated = product_updates.typescript(
            Path(__file__).parent.parent / "product-updates" / "schema.json"
        )
        self.assertIn("contributors: BundleContributor[]", generated)
        self.assertNotIn("contributors: bundleContributor[]", generated)
        self.assertIn("target_ref: string", generated)

    def test_bundle_schema_drives_types_and_rejects_missing_output_fields(self) -> None:
        path = self.content / "schema.json"
        schema = json.loads(path.read_text())
        schema["$defs"]["bundleEntry"]["properties"]["reviewerNote"] = {
            "type": "string"
        }
        schema["$defs"]["bundleEntry"]["required"].append("reviewerNote")
        self._write_json(path, schema)
        self.assertIn("reviewerNote: string", product_updates.typescript(path))
        with self.assertRaisesRegex(ValueError, "reviewerNote"):
            product_updates.build_bundle(
                self.content,
                self.inventory,
                self.dispositions,
                TARGET,
                "/product-updates/",
            )

    def test_event_rejects_unknown_source_pr(self) -> None:
        entry = self.entry_data()
        entry["sources"].append({"pr": 999})
        entry["contributors"] = [
            {
                "login": "octocat",
                "profile_url": "https://github.com/octocat",
                "source_pr": 10,
            }
        ]
        errors = product_updates.validate_event(
            {"pull_request": {"number": 10, "user": {"login": "octocat"}}},
            self.disposition_data(),
            [entry],
            self.inventory_data(),
        )
        self.assertTrue(any("#999 is not verified" in error for error in errors))

    def test_schema_rejects_unknown_fields_and_invalid_timestamps(self) -> None:
        data = self.entry_data()
        data["published_at"] = "tomorrowZ"
        data["invented"] = True
        (self.content / "entries" / f"{ENTRY_ID}.md").unlink()
        self.write_entry(data)

        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )

        self.assertTrue(any("published_at" in error for error in errors))
        self.assertTrue(
            any("invented" in error and "not allowed" in error for error in errors)
        )

    def test_direct_commit_source_and_draft_historical_dispositions(self) -> None:
        direct = "d" * 40
        inventory = self.inventory_data()
        inventory["commits"].append({"sha": direct, "subject": "Direct fix", "prs": []})
        self._write_json(self.inventory, inventory)
        dispositions = self.disposition_data()
        dispositions["items"]["pr:10"]["review"]["status"] = "draft"
        dispositions["items"][f"commit:{direct}"] = {
            "classification": "other",
            "entry_ids": [],
            "review": {"status": "draft", "evidence": ["audit"]},
        }
        self._write_json(self.dispositions, dispositions)

        errors = product_updates.validate_content(
            self.content, self.inventory, self.dispositions, TARGET
        )

        self.assertEqual([], errors)

    def test_reviewed_direct_commit_is_preserved_in_other_changes(self) -> None:
        inventory = self.inventory_data()
        inventory["commits"][0]["prs"] = []
        inventory["prs"] = []
        self._write_json(self.inventory, inventory)
        disposition = self.disposition_data()
        disposition["items"] = {
            f"commit:{COMMIT}": {
                "classification": "other",
                "entry_ids": [],
                "summary": "A direct repair",
                "review": {"status": "approved", "evidence": ["review"]},
            }
        }
        self._write_json(self.dispositions, disposition)
        (self.content / "entries" / f"{ENTRY_ID}.md").unlink()
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )
        self.assertEqual("A direct repair", bundle["other_changes"][0]["title"])
        self.assertTrue(bundle["other_changes"][0]["url"].endswith(COMMIT))

    def test_release_resolves_inline_asset_links_and_keeps_credit_association(
        self,
    ) -> None:
        self.write_entry(body=f"See [the screenshot](assets/{ENTRY_ID}/screen.png).")
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )

        body = product_updates.render_release(
            bundle, f"https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/"
        )

        self.assertNotIn(f"](assets/{ENTRY_ID}/screen.png)", body)
        self.assertIn(
            f"](https://raw.githubusercontent.com/gobifrost/bifrost/{TARGET}/product-updates/assets/{ENTRY_ID}/screen.png)",
            body,
        )
        self.assertEqual(1, body.count("screen.png"))
        self.assertIn("[octocat](https://github.com/octocat) (#10)", body)

    def test_asset_map_is_stable_and_only_imports_validated_assets(self) -> None:
        output = product_updates.asset_map(self.content)

        self.assertIn(f'"assets/{ENTRY_ID}/screen.png"', output)
        self.assertIn("?url", output)
        self.assertNotIn("../", output)

    def test_other_change_uses_reviewed_disposition_summary(self) -> None:
        dispositions = self.disposition_data()
        dispositions["items"]["pr:10"] = {
            "classification": "other",
            "entry_ids": [],
            "summary": "A reader-friendly summary",
            "category": "hardening",
            "review": {"status": "approved", "evidence": ["review"]},
        }
        self._write_json(self.dispositions, dispositions)
        bundle = product_updates.build_bundle(
            self.content, self.inventory, self.dispositions, TARGET, "/product-updates/"
        )

        self.assertEqual(
            "A reader-friendly summary", bundle["other_changes"][0]["title"]
        )
        self.assertEqual("hardening", bundle["other_changes"][0]["category"])

    def test_release_assets_are_pinned_to_content_ref_not_eligibility_target(
        self,
    ) -> None:
        content_ref = "e" * 40
        bundle = product_updates.build_bundle(
            self.content,
            self.inventory,
            self.dispositions,
            TARGET,
            "/product-updates/",
            content_ref=content_ref,
        )

        body = product_updates.render_release(
            bundle,
            f"https://raw.githubusercontent.com/gobifrost/bifrost/{content_ref}/",
        )

        self.assertEqual(content_ref, bundle["content_ref"])
        self.assertIn(f"/{content_ref}/product-updates/assets/", body)

    def test_explicit_preview_includes_staged_entry_without_landed_sources(
        self,
    ) -> None:
        data = self.entry_data()
        data["sources"] = []
        data["eligibility"] = {
            "requires_prs": [],
            "requires_commits": [],
            "staged": True,
        }
        data["review"] = {"status": "draft", "evidence": ["preview"]}
        (self.content / "entries" / f"{ENTRY_ID}.md").unlink()
        self.write_entry(data)

        bundle = product_updates.build_bundle(
            self.content,
            self.inventory,
            self.dispositions,
            TARGET,
            "/product-updates/",
            allow_draft=True,
            include_staged=True,
        )

        self.assertEqual([ENTRY_ID], [entry["id"] for entry in bundle["entries"]])


if __name__ == "__main__":
    unittest.main()
