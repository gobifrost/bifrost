#!/usr/bin/env node
/**
 * Decide which manifest entries should be re-captured based on source-glob
 * diffs against each entry's `captured_at.bifrost_sha`.
 *
 * Modes:
 *   default: only entries whose source_globs changed since their captured SHA.
 *   --full:  every entry (the post-processor's pixel-diff still gates commits).
 *
 * Outputs (stdout): comma-separated IDs to capture, suitable for
 * DOCS_CAPTURE_IDS. Empty string if nothing to do (also exit 0).
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { execSync } from "node:child_process";
import * as yaml from "js-yaml";

function parseArgs(argv) {
  const out = { full: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--docs-repo") out.docsRepo = argv[++i];
    else if (a === "--bifrost-repo") out.bifrostRepo = argv[++i];
    else if (a === "--full") out.full = true;
    else if (a === "--ids") out.ids = argv[++i];
    else if (a === "-h" || a === "--help") {
      console.log("usage: decide-captures.mjs --docs-repo <path> --bifrost-repo <path> [--full|--ids id1,id2]");
      process.exit(0);
    } else {
      console.error(`unknown arg: ${a}`);
      process.exit(2);
    }
  }
  if (!out.docsRepo || !out.bifrostRepo) {
    console.error("--docs-repo and --bifrost-repo are required");
    process.exit(2);
  }
  return out;
}

function pathChanged(repo, sinceSha, globs) {
  if (!sinceSha) return true; // never captured before
  if (!globs || !globs.length) return true; // no globs = always recapture
  try {
    execSync("git rev-parse --verify " + sinceSha, { cwd: repo, stdio: "ignore" });
  } catch {
    return true; // unknown sha — treat as changed
  }
  try {
    execSync(`git diff --quiet ${sinceSha} HEAD -- ${globs.map((g) => `'${g}'`).join(" ")}`, {
      cwd: repo,
      stdio: "ignore",
    });
    return false;
  } catch {
    return true;
  }
}

function main() {
  const args = parseArgs(process.argv);
  const manifestPath = resolve(args.docsRepo, "screenshots.yaml");
  if (!existsSync(manifestPath)) {
    console.error(`manifest not found at ${manifestPath}`);
    process.exit(1);
  }
  const manifest = yaml.load(readFileSync(manifestPath, "utf8"));

  // External entries (Azure portal, VSCode, etc.) are never candidates.
  const captureable = manifest.entries.filter((e) => !e.external);

  if (args.ids) {
    const wanted = args.ids.split(",").map((s) => s.trim()).filter(Boolean);
    const valid = new Set(captureable.map((e) => e.id));
    const filtered = wanted.filter((id) => valid.has(id));
    process.stdout.write(filtered.join(","));
    return;
  }

  if (args.full) {
    process.stdout.write(captureable.map((e) => e.id).join(","));
    return;
  }

  const candidates = [];
  for (const entry of captureable) {
    const sinceSha = entry.captured_at?.bifrost_sha ?? null;
    const globs = entry.source_globs ?? [];
    if (pathChanged(args.bifrostRepo, sinceSha, globs)) {
      candidates.push(entry.id);
    }
  }
  process.stdout.write(candidates.join(","));
}

main();
