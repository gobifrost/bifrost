/**
 * Platform globals for app-bundler external resolution.
 *
 * Apps built by the platform's esbuild bundler emit bare specifiers like
 * `import { useState } from "react"`. The static import map in
 * `client/index.html` resolves each bare specifier to a stub URL like
 * `/__bifrost_modules/react.js`. Each stub re-exports from the
 * corresponding `globalThis.__bifrost_*` key — and this function is what
 * populates those keys with the host SPA's already-loaded copies.
 *
 * Must run once at startup, before any dynamic import() of an app bundle.
 * `main.tsx` calls it before any other initialization.
 *
 * Sharing module instances across host and dynamically-loaded bundles
 * avoids the "two Reacts" hooks failure: if a bundle gets a fresh copy
 * of React from a CDN, its useContext call returns null instead of the
 * host's context value because the bundle's React holds different
 * internal context objects.
 */
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as ReactJSXRuntime from "react/jsx-runtime";
import * as ReactJSXDevRuntime from "react/jsx-dev-runtime";
import * as ReactRouterDOM from "react-router-dom";
import * as LucideReact from "lucide-react";
import { $ as platformScope } from "./app-code-runtime";

declare global {
	interface Window {
		__bifrost_react: typeof React;
		__bifrost_react_dom: typeof ReactDOM;
		__bifrost_react_dom_client: typeof ReactDOMClient;
		__bifrost_react_jsx_runtime: typeof ReactJSXRuntime;
		__bifrost_react_jsx_dev_runtime: typeof ReactJSXDevRuntime;
		__bifrost_react_router_dom: typeof ReactRouterDOM;
		__bifrost_lucide_react: typeof LucideReact;
		__bifrost_platform: typeof platformScope;
	}
}

let initialized = false;

export function initReactShim(): void {
	if (initialized) return;
	initialized = true;

	const g = globalThis as unknown as Window;
	g.__bifrost_react = React;
	g.__bifrost_react_dom = ReactDOM;
	g.__bifrost_react_dom_client = ReactDOMClient;
	g.__bifrost_react_jsx_runtime = ReactJSXRuntime;
	g.__bifrost_react_jsx_dev_runtime = ReactJSXDevRuntime;
	g.__bifrost_react_router_dom = ReactRouterDOM;
	g.__bifrost_lucide_react = LucideReact;
	g.__bifrost_platform = platformScope;
}
