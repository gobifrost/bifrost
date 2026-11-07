import assert from "node:assert/strict";
import {
	existsSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { initializeDocsCapture, writeCaptureResult } from "./docs-results.mjs";

function workspace(t) {
	const directory = mkdtempSync(join(tmpdir(), "bifrost-docs-results-"));
	t.after(() => rmSync(directory, { recursive: true, force: true }));
	return directory;
}

test("replacement workers retain earlier captures and failures", async (t) => {
	const root = workspace(t);
	initializeDocsCapture(["--project=docs"], root);
	writeCaptureResult(root, { id: "before", status: "captured" });
	writeCaptureResult(root, { id: "failed", status: "error" });
	const replacement = await import("./docs-results.mjs?replacement-worker");
	replacement.writeCaptureResult(root, { id: "after", status: "captured" });
	assert.deepEqual(
		JSON.parse(
			readFileSync(join(root, ".tmp-captures/results.json"), "utf8"),
		),
		[
			{ id: "before", status: "captured" },
			{ id: "failed", status: "error" },
			{ id: "after", status: "captured" },
		],
	);
});

test("a new docs launch clears the previous index without affecting other projects", (t) => {
	const root = workspace(t);
	initializeDocsCapture(["--project", "docs"], root);
	writeCaptureResult(root, { id: "previous-run", status: "error" });
	const filename = join(root, ".tmp-captures/results.json");
	initializeDocsCapture(["--project=admin"], root);
	assert.equal(existsSync(filename), true);
	initializeDocsCapture(["--project=docs"], root);
	assert.equal(existsSync(filename), false);
});

test("malformed prior results fail visibly instead of discarding capture history", (t) => {
	const root = workspace(t);
	initializeDocsCapture(["--project=docs"], root);
	const filename = join(root, ".tmp-captures/results.json");
	writeFileSync(filename, "broken");
	assert.throws(
		() => writeCaptureResult(root, { id: "next", status: "captured" }),
		SyntaxError,
	);
	assert.equal(readFileSync(filename, "utf8"), "broken");
});
