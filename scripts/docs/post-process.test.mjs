import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";

import * as yaml from "js-yaml";
import { PNG } from "pngjs";

const scriptPath = join(import.meta.dirname, "post-process.mjs");

function writePng(path) {
  mkdirSync(dirname(path), { recursive: true });
  const png = new PNG({ width: 1, height: 1 });
  png.data.set([15, 25, 35, 255]);
  writeFileSync(path, PNG.sync.write(png));
}

function initializeGitRepo(path) {
  mkdirSync(path, { recursive: true });
  execFileSync("git", ["init", "--quiet"], { cwd: path });
  execFileSync("git", ["config", "user.email", "docs-test@gobifrost.com"], { cwd: path });
  execFileSync("git", ["config", "user.name", "Docs Test"], { cwd: path });
  writeFileSync(join(path, "marker"), "capture source\n");
  execFileSync("git", ["add", "marker"], { cwd: path });
  execFileSync("git", ["commit", "--quiet", "-m", "capture source"], { cwd: path });
  return execFileSync("git", ["rev-parse", "HEAD"], { cwd: path, encoding: "utf8" }).trim();
}

test("an unchanged successful capture advances the manifest watermark", () => {
  const root = mkdtempSync(join(tmpdir(), "bifrost-docs-post-process-"));
  const docsRepo = join(root, "docs");
  const bifrostRepo = join(root, "bifrost");
  const expectedSha = initializeGitRepo(bifrostRepo);
  const imagePath = join(docsRepo, "public", "images", "example.png");
  const tempPath = join(docsRepo, ".tmp-captures", "example.png");

  writePng(imagePath);
  writePng(tempPath);
  writeFileSync(
    join(docsRepo, "screenshots.yaml"),
    yaml.dump({
      defaults: {},
      entries: [
        {
          id: "example",
          image: "public/images/example.png",
          captured_at: { bifrost_sha: "old-sha", timestamp: "2026-01-01T00:00:00.000Z" },
        },
      ],
    }),
  );
  writeFileSync(
    join(docsRepo, ".tmp-captures", "results.json"),
    JSON.stringify([{ id: "example", status: "captured", tempPath: "/docs/.tmp-captures/example.png" }]),
  );

  const output = execFileSync(
    process.execPath,
    [scriptPath, "--docs-repo", docsRepo, "--bifrost-repo", bifrostRepo],
    { encoding: "utf8" },
  );
  const summary = JSON.parse(output);
  const manifest = yaml.load(readFileSync(join(docsRepo, "screenshots.yaml"), "utf8"));

  assert.deepEqual(summary.committed, []);
  assert.equal(summary.unchanged[0].id, "example");
  assert.equal(manifest.entries[0].captured_at.bifrost_sha, expectedSha);
  assert.notEqual(manifest.entries[0].captured_at.timestamp, "2026-01-01T00:00:00.000Z");
});
