import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";

import * as yaml from "js-yaml";
import { PNG } from "pngjs";

const scriptPath = join(import.meta.dirname, "post-process.mjs");
const decideCapturesScriptPath = join(import.meta.dirname, "decide-captures.mjs");
const bootstrapManifestScriptPath = join(import.meta.dirname, "bootstrap-manifest.mjs");

function writePng(path, { width = 1, height = 1, pixels = [15, 25, 35, 255] } = {}) {
  mkdirSync(dirname(path), { recursive: true });
  const png = new PNG({ width, height });
  png.data.set(pixels);
  writeFileSync(path, PNG.sync.write(png));
}

function temporaryDirectory(t, prefix) {
  const root = mkdtempSync(join(tmpdir(), prefix));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
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

test("an unchanged successful capture advances the manifest watermark", (t) => {
  const root = temporaryDirectory(t, "bifrost-docs-post-process-");
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

test("a capture inherited through a YAML merge keeps its crop configuration", (t) => {
  const root = temporaryDirectory(t, "bifrost-docs-post-process-");
  const docsRepo = join(root, "docs");
  const bifrostRepo = join(root, "bifrost");
  initializeGitRepo(bifrostRepo);
  const imagePath = join(docsRepo, "public", "images", "example.png");
  const tempPath = join(docsRepo, ".tmp-captures", "example.png");

  writePng(imagePath);
  writePng(tempPath, {
    width: 2,
    pixels: [200, 210, 220, 255, 15, 25, 35, 255],
  });
  writeFileSync(
    join(docsRepo, "screenshots.yaml"),
    `capture_defaults: &capture_defaults
  capture:
    crop:
      x: 1
      y: 0
      width: 1
      height: 1
entries:
  - <<: *capture_defaults
    id: example
    image: public/images/example.png
`,
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

  assert.deepEqual(summary.committed, []);
  assert.equal(summary.unchanged[0].id, "example");
  assert.deepEqual(PNG.sync.read(readFileSync(imagePath)).data, Buffer.from([15, 25, 35, 255]));
});

test("a visual change above the replacement threshold commits the new capture", (t) => {
  const root = temporaryDirectory(t, "bifrost-docs-post-process-");
  const docsRepo = join(root, "docs");
  const bifrostRepo = join(root, "bifrost");
  initializeGitRepo(bifrostRepo);
  const imagePath = join(docsRepo, "public", "images", "example.png");
  const tempPath = join(docsRepo, ".tmp-captures", "example.png");

  writePng(imagePath, {
    width: 2,
    pixels: [15, 25, 35, 255, 15, 25, 35, 255],
  });
  writePng(tempPath, {
    width: 2,
    pixels: [200, 210, 220, 255, 15, 25, 35, 255],
  });
  writeFileSync(
    join(docsRepo, "screenshots.yaml"),
    yaml.dump({
      defaults: {},
      entries: [{ id: "example", image: "public/images/example.png" }],
    }),
  );
  writeFileSync(
    join(docsRepo, ".tmp-captures", "results.json"),
    JSON.stringify([{ id: "example", status: "captured", tempPath: "/docs/.tmp-captures/example.png" }]),
  );

  const output = execFileSync(
    process.execPath,
    [scriptPath, "--docs-repo", docsRepo, "--bifrost-repo", bifrostRepo, "--threshold", "0.4"],
    { encoding: "utf8" },
  );
  const summary = JSON.parse(output);

  assert.equal(summary.committed[0].id, "example");
  assert.equal(summary.committed[0].diffPct, 0.5);
  assert.deepEqual(
    PNG.sync.read(readFileSync(imagePath)).data,
    Buffer.from([200, 210, 220, 255, 15, 25, 35, 255]),
  );
});

test("capture selection excludes an external entry inherited through a YAML merge", (t) => {
  const root = temporaryDirectory(t, "bifrost-docs-decide-captures-");
  writeFileSync(
    join(root, "screenshots.yaml"),
    `external_entry: &external_entry
  external: true
entries:
  - <<: *external_entry
    id: provider-console
    image: public/images/provider-console.png
`,
  );

  const output = execFileSync(
    process.execPath,
    [decideCapturesScriptPath, "--docs-repo", root, "--bifrost-repo", root, "--full"],
    { encoding: "utf8" },
  );

  assert.equal(output, "");
});

test("manifest bootstrap retains a title inherited through a YAML merge", (t) => {
  const root = temporaryDirectory(t, "bifrost-docs-bootstrap-");
  const contentPath = join(root, "src", "content", "docs", "page.mdx");
  mkdirSync(dirname(contentPath), { recursive: true });
  writeFileSync(
    contentPath,
    `---
defaults: &defaults
  title: Merged Screenshot Title
<<: *defaults
---
![Example](../assets/example.png)
`,
  );

  execFileSync(
    process.execPath,
    [bootstrapManifestScriptPath, "--docs-repo", root, "--bifrost-repo", join(import.meta.dirname, "..", "..")],
    { encoding: "utf8" },
  );
  const manifest = yaml.load(readFileSync(join(root, "screenshots.yaml"), "utf8"));

  assert.equal(manifest.entries[0].diataxis.heading, "Merged Screenshot Title");
});
