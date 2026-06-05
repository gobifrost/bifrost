#!/usr/bin/env node
/**
 * Bundle the `bifrost` web SDK into a single ESM file that a standalone_v2 app
 * resolves via `import { BifrostProvider, useTable, useWorkflow } from "bifrost"`.
 *
 * The SDK source (provider, tables, hooks) lives in the client repo at
 * client/src/lib/app-sdk and is COPYied into the api image at build time (see
 * Dockerfile). Its ONLY dependency is `react`/`react-dom` (peer deps — hooks
 * need React; data hooks use plain `fetch` + `useState`, no data library).
 * React stays EXTERNAL so the app + SDK share one React instance. The only
 * non-runtime cross-project reference is `import type { components } from
 * "@/lib/v1"`, which esbuild drops (type-only imports carry no runtime code).
 *
 * Usage: node build_sdk.js <srcDir> <outFile>
 *   srcDir : directory holding the SDK .ts/.tsx sources + index.ts barrel
 *   outFile: path to write the bundled index.mjs
 */
const esbuild = require("esbuild");
const path = require("path");

const [, , srcDir, outFile] = process.argv;
if (!srcDir || !outFile) {
  console.error("usage: build_sdk.js <srcDir> <outFile>");
  process.exit(2);
}

esbuild
  .build({
    entryPoints: [path.join(srcDir, "index.ts")],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2020",
    outfile: outFile,
    jsx: "automatic",
    // Peer deps kept external: react (hooks need it — and a second copy is the
    // classic "Invalid hook call" crash) and lucide-react (icons in
    // BifrostHeader; the app already has it). Everything else is plain `fetch`.
    external: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "lucide-react",
    ],
    logLevel: "warning",
  })
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
