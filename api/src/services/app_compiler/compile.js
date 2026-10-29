#!/usr/bin/env node
/**
 * Server-side Babel compiler for Bifrost app files.
 * Replicates the exact pipeline from client/src/lib/app-code-compiler.ts.
 *
 * Input (stdin):  {"files": [{"path": "pages/index.tsx", "source": "..."}]}
 * Output (stdout): {"results": [{"path": "...", "compiled": "...", "error": null}]}
 */
const { transform } = require("@babel/standalone");

function preprocessImports(source) {
  // Use `var` instead of `const` so these don't conflict with scope
  // parameters injected by the runtime (e.g. Outlet, Card, etc.)
  // `var` can redeclare function parameters without error in strict mode.
  let result = source.replace(
    /^\s*import\s+(\{[^}]*\})\s+from\s+["']bifrost["']\s*;?\s*$/gm,
    "var $1 = $;"
  );
  result = result.replace(
    /^\s*import\s+(\w+)\s+from\s+["']bifrost["']\s*;?\s*$/gm,
    "var $1 = $.default || $;"
  );
  result = result.replace(
    /^\s*import\s+(\w+)\s*,\s*(\{[^}]*\})\s+from\s+["']bifrost["']\s*;?\s*$/gm,
    "var $1 = $.default || $;\nvar $2 = $;"
  );
  return result;
}

function preprocessRelativeImports(source) {
  // Strip relative imports — custom components are auto-injected at runtime,
  // and same-app modules are resolved by the runtime loader.
  // Matches: import { X } from "./foo", import X from "../bar", import * as X from "../../baz"
  return source.replace(
    /^\s*import\s+(?:\{[^}]*\}|\w+|\*\s+as\s+\w+)(?:\s*,\s*(?:\{[^}]*\}|\w+))?\s+from\s+["']\.\.?\/[^"']+["']\s*;?\s*$/gm,
    ""
  );
}

function preprocessExternalImports(source) {
  // Use `var` instead of `const` — same reason as preprocessImports:
  // avoids redeclaration errors with scope parameters in strict mode.

  // Named imports: import { X, Y } from "pkg" → var { X, Y } = $deps["pkg"];
  let result = source.replace(
    /^\s*import\s+(\{[^}]*\})\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'var $1 = $$deps["$2"];'
  );

  // Default imports: import X from "pkg" → var X = ($deps["pkg"].default || $deps["pkg"]);
  result = result.replace(
    /^\s*import\s+(\w+)\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'var $1 = ($$deps["$2"].default || $$deps["$2"]);'
  );

  // Namespace imports: import * as X from "pkg" → var X = $deps["pkg"];
  result = result.replace(
    /^\s*import\s+\*\s+as\s+(\w+)\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'var $1 = $$deps["$2"];'
  );

  // Mixed imports: import X, { Y, Z } from "pkg"
  result = result.replace(
    /^\s*import\s+(\w+)\s*,\s*(\{[^}]*\})\s+from\s+["']([^"']+)["']\s*;?\s*$/gm,
    'var $1 = ($$deps["$3"].default || $$deps["$3"]);\nvar $2 = $$deps["$3"];'
  );

  return result;
}

function postprocessExports(compiled) {
  let code = compiled;
  let defaultExport = null;
  const namedExports = [];

  const defaultFuncMatch = code.match(/export\s+default\s+function\s+(\w+)/);
  if (defaultFuncMatch) {
    const funcName = defaultFuncMatch[1];
    code = code.replace(/export\s+default\s+function\s+(\w+)/, "function $1");
    code += `\n__defaultExport__ = ${funcName};`;
    defaultExport = funcName;
  }

  if (!defaultExport && code.includes("export default function(")) {
    code = code.replace(/export\s+default\s+function\s*\(/, "__defaultExport__ = function(");
    defaultExport = "__defaultExport__";
  }

  const defaultVarMatch = code.match(/export\s+default\s+(\w+)\s*;/);
  if (!defaultExport && defaultVarMatch) {
    const varName = defaultVarMatch[1];
    code = code.replace(/export\s+default\s+\w+\s*;/, "");
    code += `\n__defaultExport__ = ${varName};`;
    defaultExport = varName;
  }

  for (const match of code.matchAll(/export\s+function\s+(\w+)/g)) {
    namedExports.push(match[1]);
  }
  for (const match of code.matchAll(/export\s+(?:const|let|var)\s+(\w+)/g)) {
    namedExports.push(match[1]);
  }

  code = code.replace(/export\s+\{[^}]*\}\s*;?/g, "");
  code = code.replace(/export\s+(const|let|var|function|class)\s+/g, "$1 ");

  if (namedExports.length > 0) {
    code += "\n__exports__ = {};";
    for (const name of namedExports) {
      code += `\n__exports__.${name} = ${name};`;
    }
  }

  if (!defaultExport && namedExports.length > 0) {
    defaultExport = namedExports[0];
    code += `\n__defaultExport__ = ${defaultExport};`;
  }

  return { code, defaultExport, namedExports };
}

function compileFile(source, path) {
  try {
    let preprocessed = preprocessImports(source);      // bifrost imports → $
    preprocessed = preprocessRelativeImports(preprocessed); // strip relative imports (auto-injected)
    preprocessed = preprocessExternalImports(preprocessed); // remaining → $deps["pkg"]
    const result = transform(preprocessed, {
      filename: path || "component.tsx",
      presets: [
        ["react", { runtime: "classic" }],
        ["typescript", { onlyRemoveTypeImports: false }],
      ],
      plugins: ["transform-optional-chaining", "transform-nullish-coalescing-operator"],
      sourceType: "module",
    });

    if (!result.code) {
      return { compiled: null, error: "Compilation produced no output" };
    }

    const { code, defaultExport, namedExports } = postprocessExports(result.code);
    return { compiled: code, defaultExport, namedExports, error: null };
  } catch (err) {
    return { compiled: null, error: err.message || "Compilation failed" };
  }
}

// Read JSON from stdin
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  try {
    const { files } = JSON.parse(input);
    const results = files.map((f) => ({
      path: f.path,
      ...compileFile(f.source, f.path),
    }));
    process.stdout.write(JSON.stringify({ results }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ error: err.message }));
    process.exit(1);
  }
});
