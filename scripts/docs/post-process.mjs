#!/usr/bin/env node
/**
 * Host-side post-processor for the docs screenshot pipeline.
 *
 * After the capture spec finishes and `<docs>/.tmp-captures/results.json`
 * exists, this script:
 *
 *   1. Reads each captured PNG.
 *   2. Applies `capture.crop` (if defined) to extract a sub-region.
 *   3. Overlays `capture.callouts` rectangles + labels.
 *   4. Compares the result to the committed PNG at `<docs>/<entry.image>`.
 *      If pixel diff exceeds threshold, replaces the committed PNG and
 *      marks the entry's `captured_at` with the current bifrost SHA.
 *      Otherwise discards the temp file silently.
 *
 * Inputs:
 *   --docs-repo <path>     required
 *   --bifrost-repo <path>  required (for git rev-parse HEAD)
 *   --threshold <pct>      default 0.1 (% of pixels that must differ)
 *
 * Outputs (to stdout):
 *   JSON summary { committed, unchanged, errors, missing }
 */
import { readFileSync, writeFileSync, existsSync, unlinkSync, mkdirSync, renameSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { execSync } from "node:child_process";
import * as yaml from "js-yaml";
import sharp from "sharp";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

function parseArgs(argv) {
  const out = { threshold: 0.001 };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--docs-repo") out.docsRepo = argv[++i];
    else if (a === "--bifrost-repo") out.bifrostRepo = argv[++i];
    else if (a === "--threshold") out.threshold = parseFloat(argv[++i]);
    else if (a === "-h" || a === "--help") {
      console.log("usage: post-process.mjs --docs-repo <path> --bifrost-repo <path> [--threshold <0..1>]");
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

function bifrostHead(bifrostRepo) {
  return execSync("git rev-parse HEAD", { cwd: bifrostRepo, encoding: "utf8" }).trim();
}

async function applyAnnotations(imageBuf, capture, defaults) {
  let img = sharp(imageBuf);
  const meta = await img.metadata();
  let width = meta.width ?? 0;
  let height = meta.height ?? 0;
  let buf = imageBuf;

  // Per-entry crop wins; fall back to manifest-wide defaults.crop so a single
  // value can strip sidebar/header chrome from every screenshot.
  const effectiveCrop = capture?.crop ?? defaults?.crop;
  if (effectiveCrop) {
    const c = effectiveCrop;
    const left = Math.max(0, Math.min(c.x, width - 1));
    const top = Math.max(0, Math.min(c.y, height - 1));
    const w = Math.max(1, Math.min(c.width, width - left));
    const h = Math.max(1, Math.min(c.height, height - top));
    buf = await sharp(buf).extract({ left, top, width: w, height: h }).png().toBuffer();
    const cropMeta = await sharp(buf).metadata();
    width = cropMeta.width ?? w;
    height = cropMeta.height ?? h;
  }

  const callouts = capture?.callouts ?? [];
  if (callouts.length) {
    // For each callout, render an SVG overlay (rectangle + optional label badge).
    const svgParts = callouts.map((co, idx) => {
      const color = co.color ?? "#f59e0b";
      const stroke = 4;
      const x = co.x;
      const y = co.y;
      const w = co.width;
      const h = co.height;
      const labelText = co.label ?? String(idx + 1);
      const labelSize = 28;
      const labelX = Math.max(0, x - labelSize / 2);
      const labelY = Math.max(0, y - labelSize / 2);
      return `
        <rect x="${x}" y="${y}" width="${w}" height="${h}"
              fill="none" stroke="${color}" stroke-width="${stroke}" rx="4" />
        <circle cx="${labelX + labelSize / 2}" cy="${labelY + labelSize / 2}"
                r="${labelSize / 2}" fill="${color}" />
        <text x="${labelX + labelSize / 2}" y="${labelY + labelSize / 2 + 5}"
              font-family="Inter, system-ui, sans-serif" font-size="16"
              font-weight="700" fill="white" text-anchor="middle">${escapeXml(labelText)}</text>
      `;
    });
    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
           viewBox="0 0 ${width} ${height}">
        ${svgParts.join("\n")}
      </svg>
    `;
    buf = await sharp(buf)
      .composite([{ input: Buffer.from(svg), top: 0, left: 0 }])
      .png()
      .toBuffer();
  }

  return buf;
}

function escapeXml(s) {
  return String(s).replace(/[<>&"']/g, (c) => ({
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
  })[c]);
}

function pngFromBuffer(buf) {
  return PNG.sync.read(buf);
}

function diffPercent(aBuf, bBuf) {
  const a = pngFromBuffer(aBuf);
  const b = pngFromBuffer(bBuf);
  if (a.width !== b.width || a.height !== b.height) return 1;
  const diff = new PNG({ width: a.width, height: a.height });
  const numDiff = pixelmatch(a.data, b.data, diff.data, a.width, a.height, {
    threshold: 0.1,
  });
  return numDiff / (a.width * a.height);
}

async function main() {
  const args = parseArgs(process.argv);
  const docsRepo = resolve(args.docsRepo);
  const bifrostRepo = resolve(args.bifrostRepo);
  const tmpDir = resolve(docsRepo, ".tmp-captures");
  const resultsPath = resolve(tmpDir, "results.json");
  const manifestPath = resolve(docsRepo, "screenshots.yaml");

  if (!existsSync(resultsPath)) {
    console.error(`No capture results at ${resultsPath}`);
    process.exit(1);
  }

  const results = JSON.parse(readFileSync(resultsPath, "utf8"));
  const manifest = yaml.load(readFileSync(manifestPath, "utf8"));
  const byId = new Map(manifest.entries.map((e) => [e.id, e]));
  const sha = bifrostHead(bifrostRepo);

  const summary = { committed: [], unchanged: [], errors: [], missing: [] };

  for (const r of results) {
    if (r.status !== "captured") {
      summary.errors.push({ id: r.id, message: r.message });
      continue;
    }
    const entry = byId.get(r.id);
    if (!entry) {
      summary.missing.push(r.id);
      continue;
    }
    // results.json records the container-side path (/docs/.tmp-captures/...).
    // Translate it to the host-side path so the post-processor can read it.
    const tempBasename = r.tempPath.split("/").pop();
    const hostTempPath = resolve(tmpDir, tempBasename);
    if (!existsSync(hostTempPath)) {
      summary.missing.push(r.id);
      continue;
    }

    try {
      const tempBuf = readFileSync(hostTempPath);
      const finalBuf = await applyAnnotations(tempBuf, entry.capture, manifest.defaults);
      const targetPath = resolve(docsRepo, entry.image);
      mkdirSync(dirname(targetPath), { recursive: true });

      let shouldCommit = true;
      if (existsSync(targetPath)) {
        const existing = readFileSync(targetPath);
        const pct = diffPercent(existing, finalBuf);
        if (pct < args.threshold) {
          shouldCommit = false;
          summary.unchanged.push({ id: r.id, diffPct: pct });
        } else {
          summary.committed.push({ id: r.id, diffPct: pct });
        }
      } else {
        summary.committed.push({ id: r.id, diffPct: 1, reason: "first-capture" });
      }

      if (shouldCommit) {
        // Atomic write: write to temp file then rename, so a concurrent reader
        // never sees a half-written PNG (closes CodeQL js/file-system-race).
        const targetTmp = `${targetPath}.tmp-${process.pid}-${Date.now()}`;
        writeFileSync(targetTmp, finalBuf);
        renameSync(targetTmp, targetPath);
        entry.captured_at = { bifrost_sha: sha, timestamp: new Date().toISOString() };
      }

      try {
        unlinkSync(hostTempPath);
      } catch {
        // Containers run as root; the host user may not be able to unlink.
        // Harmless — the next pipeline run wipes .tmp-captures/ anyway.
      }
    } catch (e) {
      summary.errors.push({ id: r.id, message: e instanceof Error ? e.message : String(e) });
    }
  }

  // Write back the manifest with updated captured_at fields (atomic).
  const manifestTmp = `${manifestPath}.tmp-${process.pid}-${Date.now()}`;
  writeFileSync(manifestTmp, yaml.dump(manifest, { lineWidth: 120, noRefs: true }));
  renameSync(manifestTmp, manifestPath);

  console.log(JSON.stringify(summary, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
