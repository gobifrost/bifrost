/**
 * Dump the export surface of client/src/lib/app-sdk/index.v2.ts as
 * deterministic sorted markdown. Dependency-free: parses export statements
 * with regex rather than requiring ts-morph. Output goes to stdout.
 *
 * Run: node api/scripts/skill-truth/dump-app-sdk-surface.mjs
 */

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
// This file lives at api/scripts/skill-truth/ (or /app/scripts/skill-truth/ in container).
// index.v2.ts lives at client/src/lib/app-sdk/index.v2.ts (or /client/src/lib/app-sdk/ in container).
// The relative path works in both environments:
//   host:      api/scripts/skill-truth/../../../client/src/... = client/src/...
//   container: /app/scripts/skill-truth/../../../client/src/... = /client/src/...
const INDEX_PATH = resolve(
  __dirname,
  "../../../client/src/lib/app-sdk/index.v2.ts"
);

const src = readFileSync(INDEX_PATH, "utf-8");

/** @typedef {{ name: string; kind: "value" | "type" }} Export */

/** @type {Export[]} */
const exports = [];

// Match: export { Foo, Bar } from "..."
// and:   export type { Baz } from "..."
const namedRe =
  /^export\s+(type\s+)?\{([^}]+)\}\s+from\s+["'][^"']+["']/gm;
for (const match of src.matchAll(namedRe)) {
  const isType = Boolean(match[1]);
  const names = match[2]
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  for (const raw of names) {
    // Handle "Foo as Bar" aliasing — use the exported (outer) name
    const name = raw.includes(" as ") ? raw.split(" as ")[1].trim() : raw;
    if (name) exports.push({ name, kind: isType ? "type" : "value" });
  }
}

// Match: export { Foo } (no from — local re-export or inline)
const bareRe = /^export\s+(type\s+)?\{([^}]+)\}\s*;/gm;
for (const match of src.matchAll(bareRe)) {
  const isType = Boolean(match[1]);
  const names = match[2]
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  for (const raw of names) {
    const name = raw.includes(" as ") ? raw.split(" as ")[1].trim() : raw;
    if (name) exports.push({ name, kind: isType ? "type" : "value" });
  }
}

// Deduplicate by name (value wins over type if both present)
/** @type {Map<string, "value" | "type">} */
const seen = new Map();
for (const { name, kind } of exports) {
  if (!seen.has(name) || kind === "value") seen.set(name, kind);
}

const sorted = [...seen.entries()].sort(([a], [b]) => a.localeCompare(b));

const lines = [
  "# Web SDK (v2) Surface (generated — do not edit)",
  "",
  "> Regenerate: `node api/scripts/skill-truth/dump-app-sdk-surface.mjs`. CI enforces freshness.",
  "",
];
for (const [name, kind] of sorted) {
  const tag = kind === "type" ? "type" : "value";
  lines.push(`- \`${name}\` (${tag})`);
}
lines.push("");

process.stdout.write(lines.join("\n"));
