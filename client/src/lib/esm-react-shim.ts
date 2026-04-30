/**
 * ESM React Shim — single import map covering every shared module a bundled
 * app needs from the host (React, React Router, Lucide).
 *
 * Problem: esm.sh serves its own React 19 bundle. Same version, but a
 * different JS object in memory. React hooks require the exact same object
 * instance, so any package calling useState/useContext crashes. The same
 * hazard applies to React Router (one provider tree, one consumer tree).
 *
 * Solution: expose the host modules on window globals, create blob-URL ES
 * modules that re-export from those globals, and inject one import map so
 * bare `import "react"` / `import "react-router-dom"` / `import "lucide-react"`
 * resolve to the host's copy.
 *
 * Why all of it lives here: the browser only honors specifier-resolution
 * from import maps installed before the first matching `import()` call. If
 * we inject one map at startup and then a SECOND map later (e.g. when the
 * bundle is loaded), the browser warns and drops the conflicting rules from
 * the later map. We had that bug — `BundledAppShell` injected its own map
 * with overlapping React entries, producing console warnings AND two
 * separate captured React module references on `window`. Consolidating into
 * one early map removes both problems.
 *
 * Must be called once at startup, before any dynamic import() of esm.sh
 * URLs or app bundles.
 */
import React from "react";
import ReactDOM from "react-dom";
import * as ReactJSXRuntime from "react/jsx-runtime";
import * as ReactJSXDevRuntime from "react/jsx-dev-runtime";
import * as ReactDOMClient from "react-dom/client";
import * as ReactRouterDOM from "react-router-dom";
import * as LucideReact from "lucide-react";

declare global {
	interface Window {
		__BIFROST_REACT: typeof React;
		__BIFROST_REACT_DOM: typeof ReactDOM;
		__BIFROST_REACT_JSX_RUNTIME: typeof ReactJSXRuntime;
		__BIFROST_REACT_JSX_DEV_RUNTIME: typeof ReactJSXDevRuntime;
		__BIFROST_REACT_DOM_CLIENT: typeof ReactDOMClient;
		__BIFROST_REACT_ROUTER_DOM: typeof ReactRouterDOM;
		__BIFROST_LUCIDE_REACT: typeof LucideReact;
	}
}

/**
 * Create a blob URL for an ES module that re-exports from a window global.
 */
function makeBlobModule(globalName: string, moduleObj: object): string {
	const keys = Object.keys(moduleObj).filter((k) => k !== "default");
	const lines: string[] = [
		`const m = window.${globalName};`,
		`export default m.default !== undefined ? m.default : m;`,
	];
	if (keys.length > 0) {
		lines.push(
			`export const { ${keys.join(", ")} } = m;`,
		);
	}
	const code = lines.join("\n");
	const blob = new Blob([code], { type: "application/javascript" });
	return URL.createObjectURL(blob);
}

/**
 * Inject an import map into the document so bare specifiers like
 * `import "react"` resolve to our blob URLs. Tags the script with
 * `data-bifrost-import-map` so other code (BundledAppShell) can detect it
 * and avoid injecting an overlapping map.
 */
function injectImportMap(imports: Record<string, string>): void {
	const script = document.createElement("script");
	script.type = "importmap";
	script.dataset.bifrostImportMap = "true";
	script.textContent = JSON.stringify({ imports });
	document.head.appendChild(script);
}

let initialized = false;

/**
 * Initialize the React shim. Call once at app startup before createRoot().
 */
export function initReactShim(): void {
	if (initialized) return;
	initialized = true;

	// 1. Expose on window
	window.__BIFROST_REACT = React;
	window.__BIFROST_REACT_DOM = ReactDOM;
	window.__BIFROST_REACT_JSX_RUNTIME = ReactJSXRuntime;
	window.__BIFROST_REACT_JSX_DEV_RUNTIME = ReactJSXDevRuntime;
	window.__BIFROST_REACT_DOM_CLIENT = ReactDOMClient;
	window.__BIFROST_REACT_ROUTER_DOM = ReactRouterDOM;
	window.__BIFROST_LUCIDE_REACT = LucideReact;

	// 2. Create blob URLs
	const imports: Record<string, string> = {
		react: makeBlobModule("__BIFROST_REACT", React),
		"react-dom": makeBlobModule("__BIFROST_REACT_DOM", ReactDOM),
		"react/jsx-runtime": makeBlobModule(
			"__BIFROST_REACT_JSX_RUNTIME",
			ReactJSXRuntime,
		),
		"react/jsx-dev-runtime": makeBlobModule(
			"__BIFROST_REACT_JSX_DEV_RUNTIME",
			ReactJSXDevRuntime,
		),
		"react-dom/client": makeBlobModule(
			"__BIFROST_REACT_DOM_CLIENT",
			ReactDOMClient,
		),
		"react-router-dom": makeBlobModule(
			"__BIFROST_REACT_ROUTER_DOM",
			ReactRouterDOM,
		),
		"lucide-react": makeBlobModule(
			"__BIFROST_LUCIDE_REACT",
			LucideReact,
		),
	};

	// 3. Inject import map
	injectImportMap(imports);
}
