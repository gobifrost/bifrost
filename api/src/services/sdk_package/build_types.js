#!/usr/bin/env node
/** Emit self-contained SDK declarations using the supported TypeScript compiler. */
const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const [, , srcDir, outDir] = process.argv;
if (!srcDir || !outDir) {
  console.error("usage: build_types.js <srcDir> <outDir>");
  process.exit(2);
}

const config = JSON.parse(fs.readFileSync(path.join(__dirname, "tsconfig.types.json"), "utf8"));
config.compilerOptions.outDir = outDir;
config.compilerOptions.rootDir = srcDir;
config.compilerOptions.paths["@/lib/v1"] = [path.join(srcDir, "v1.d.ts")];
config.compilerOptions.paths.react = [path.join(__dirname, "node_modules", "@types", "react", "index.d.ts")];
config.compilerOptions.paths["react/jsx-runtime"] = [path.join(__dirname, "node_modules", "@types", "react", "jsx-runtime.d.ts")];
config.compilerOptions.paths["lucide-react"] = [path.join(__dirname, "node_modules", "lucide-react", "dist", "lucide-react.d.ts")];
config.include = [path.join(srcDir, "**/*.ts"), path.join(srcDir, "**/*.tsx")];
config.exclude = [path.join(srcDir, "**/*.test.ts"), path.join(srcDir, "**/*.test.tsx")];
const configPath = path.join(path.dirname(outDir), "sdk-types.tsconfig.json");
fs.writeFileSync(configPath, JSON.stringify(config));
childProcess.execFileSync(path.join(__dirname, "node_modules", ".bin", "tsc"), ["-p", configPath], {
  stdio: "pipe",
});

for (const entry of fs.readdirSync(outDir, { recursive: true })) {
  if (!entry.endsWith(".d.ts")) continue;
  const file = path.join(outDir, entry);
  fs.writeFileSync(
    file,
    fs.readFileSync(file, "utf8")
      .replaceAll('"@/lib/v1"', '"./v1"')
      .replaceAll("'@/lib/v1'", "'./v1'"),
  );
}
fs.copyFileSync(path.join(srcDir, "v1.d.ts"), path.join(outDir, "v1.d.ts"));
