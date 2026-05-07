/**
 * Drift tests for the platform-modules registry.
 *
 * These tests guard the contract between four files that all have to
 * agree on which modules the platform shares with dynamically-loaded app
 * bundles:
 *
 * 1. `client/src/lib/platform-modules.ts`        — the registry
 * 2. `client/src/lib/esm-react-shim.ts`         — populates globalThis.__bifrost_*
 * 3. `client/index.html`                         — static <script type="importmap">
 * 4. `client/src/build-plugins/bifrost-module-stubs.ts` — emits stub files
 *
 * If any of these drift apart, app bundles silently fail to resolve
 * imports in production. Each test below pins one piece of the contract.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { PLATFORM_MODULES, stubUrlFor } from "./platform-modules";

const __dirname = dirname(fileURLToPath(import.meta.url));
const clientRoot = resolve(__dirname, "../..");

describe("PLATFORM_MODULES registry", () => {
	it("each specifier resolves to a real module that exposes named exports", async () => {
		for (const { specifier } of PLATFORM_MODULES) {
			const mod = await import(/* @vite-ignore */ specifier);
			const keys = Object.keys(mod).filter((k) => k !== "default");
			expect(keys.length, `expected named exports from ${specifier}`).toBeGreaterThan(0);
		}
	});

	it("stubUrlFor produces a stable, slash-free filename", () => {
		expect(stubUrlFor("react")).toBe("/__bifrost_modules/react.js");
		expect(stubUrlFor("react/jsx-runtime")).toBe("/__bifrost_modules/react-jsx-runtime.js");
		expect(stubUrlFor("react-dom/client")).toBe("/__bifrost_modules/react-dom-client.js");
	});

	it("each globalKey is unique", () => {
		const keys = PLATFORM_MODULES.map((m) => m.globalKey);
		expect(new Set(keys).size).toBe(keys.length);
	});

	it("each specifier is unique", () => {
		const specifiers = PLATFORM_MODULES.map((m) => m.specifier);
		expect(new Set(specifiers).size).toBe(specifiers.length);
	});
});

describe("static importmap in client/index.html", () => {
	const html = readFileSync(resolve(clientRoot, "index.html"), "utf8");
	const mapMatch = html.match(/<script type="importmap">([\s\S]*?)<\/script>/);
	const importmap = mapMatch ? JSON.parse(mapMatch[1]) : null;

	it("contains a <script type=\"importmap\"> element", () => {
		expect(importmap).not.toBeNull();
	});

	it("has an entry for every PLATFORM_MODULES specifier", () => {
		expect(importmap).not.toBeNull();
		for (const { specifier } of PLATFORM_MODULES) {
			expect(importmap.imports[specifier], `missing importmap entry for ${specifier}`).toBe(
				stubUrlFor(specifier),
			);
		}
	});

	it("does not contain entries for specifiers not in PLATFORM_MODULES", () => {
		expect(importmap).not.toBeNull();
		const registered = new Set(PLATFORM_MODULES.map((m) => m.specifier));
		for (const key of Object.keys(importmap.imports)) {
			expect(
				registered.has(key),
				`importmap has stale entry "${key}" — remove or add to PLATFORM_MODULES`,
			).toBe(true);
		}
	});

	it("appears in <head> before any <script type=\"module\">", () => {
		const importmapIdx = html.indexOf('type="importmap"');
		const moduleScriptIdx = html.indexOf('type="module"');
		expect(importmapIdx).toBeGreaterThan(-1);
		expect(moduleScriptIdx).toBeGreaterThan(-1);
		expect(importmapIdx).toBeLessThan(moduleScriptIdx);
	});
});

describe("esm-react-shim populates every globalKey", () => {
	const shim = readFileSync(
		resolve(clientRoot, "src/lib/esm-react-shim.ts"),
		"utf8",
	);

	it.each(PLATFORM_MODULES.map((m) => [m.specifier, m.globalKey] as const))(
		"%s -> globalThis.%s",
		(_specifier, globalKey) => {
			// Match `g.__bifrost_react = React;` style assignment.
			const assignment = new RegExp(`g\\.${globalKey}\\s*=`);
			expect(shim).toMatch(assignment);
		},
	);
});
