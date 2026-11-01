#!/usr/bin/env node
/**
 * Build either the small installed binding or the hosted SDK factory artifact.
 *
 * The SDK source (provider, tables, hooks) lives in the client repo at
 * client/src/lib/app-sdk and is COPYied into the api image at build time (see
 * Dockerfile). Its ONLY dependency is `react`/`react-dom` (peer deps — hooks
 * need React; data hooks use plain `fetch` + `useState`, no data library).
 * React stays EXTERNAL so the app + SDK share one React instance. The only
 * non-runtime cross-project reference is `import type { components } from
 * "@/lib/v1"`, which esbuild drops (type-only imports carry no runtime code).
 *
 * Usage: node build_sdk.js <binding|hosted> <srcDir> <outFile>
 *   srcDir : directory holding the SDK .ts/.tsx sources + index.ts barrel
 *   outFile: path to write the bundled index.mjs
 */
const esbuild = require("esbuild");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const [, , mode, srcDir, outFile] = process.argv;
if (!mode || !srcDir || !outFile || !["binding", "hosted"].includes(mode)) {
  console.error("usage: build_sdk.js <binding|hosted> <srcDir> <outFile>");
  process.exit(2);
}

const hosted = mode === "hosted";
const external = hosted
  ? ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime", "bifrost-sdk-errors"]
  : ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"];

const errorsAlias = {
  name: "hosted-sdk-errors-peer",
  setup(build) {
    if (hosted) {
      build.onResolve({ filter: /^\.\/errors$/ }, () => ({
        path: "bifrost-sdk-errors",
        external: true,
      }));
    }
  },
};

const bundledSdkDependency = {
  name: "bundled-sdk-dependency",
  setup(build) {
    if (hosted) {
      build.onResolve({ filter: /^lucide-react$/ }, () => ({
        path: require.resolve("lucide-react"),
      }));
    }
  },
};

esbuild
  .build({
    entryPoints: [path.join(srcDir, hosted ? "index.v2.ts" : "hosted-binding.tsx")],
    bundle: true,
    format: hosted ? "cjs" : "esm",
    platform: "browser",
    target: "es2020",
    outfile: outFile,
    write: !hosted,
    jsx: "automatic",
    external,
    plugins: [errorsAlias, bundledSdkDependency],
    logLevel: "warning",
  })
  .then((result) => {
    if (!hosted) return;

    const factorySource = result.outputFiles[0].text;
    // The factory id covers the actual SDK implementation. The served script
    // deliberately has its own content hash, avoiding a self-referential
    // registration key while keeping the registry key content-derived.
    const factoryId = crypto.createHash("sha256").update(factorySource).digest("hex");
    const artifact = `/* Generated Bifrost App SDK factory; do not edit. */\n(function () {\n  "use strict";\n  let registry = globalThis.__BIFROST_APP_SDK_FACTORIES_V1__;\n  if (!registry) {\n    registry = Object.create(null);\n    globalThis.__BIFROST_APP_SDK_FACTORIES_V1__ = registry;\n  }\n  const factoryId = ${JSON.stringify(factoryId)};\n  registry[factoryId] = Object.freeze({\n    createSdk(peers) {\n      if (!peers || !peers.react || !peers.jsxRuntime || !peers.errors) {\n        throw new Error("Bifrost App SDK factory requires react, jsxRuntime, and errors peers");\n      }\n      const module = { exports: {} };\n      const require = (name) => {\n        switch (name) {\n          case "react": return peers.react;\n          case "react/jsx-runtime":\n          case "react/jsx-dev-runtime": return peers.jsxRuntime;\n          case "bifrost-sdk-errors": return peers.errors;\n          default: throw new Error(\`Unsupported Bifrost App SDK peer: \${name}\`);\n        }\n      };\n      (function (require, module, exports) {\n${factorySource}\n      })(require, module, module.exports);\n      return module.exports;\n    },\n  });\n})();\n`;
    fs.writeFileSync(outFile, artifact);
    const fingerprint = crypto.createHash("sha256").update(artifact).digest("hex");
    const integrity = "sha256-" + crypto.createHash("sha256").update(artifact).digest("base64");
    fs.writeFileSync(
      `${outFile}.json`,
      JSON.stringify({ api_major: 1, factory_version: 1, factory_id: factoryId, fingerprint, integrity }),
    );
  })
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
