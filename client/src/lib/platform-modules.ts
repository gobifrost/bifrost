/**
 * Platform modules exposed to dynamically-loaded app bundles via the static
 * import map in `client/index.html`.
 *
 * Apps built by the platform's esbuild bundler emit bare specifiers like
 * `import { useState } from "react"`. The static import map resolves each
 * platform key to `/__bifrost_modules/<file>.js` — small ESM stubs served
 * by the `bifrost-module-stubs` Vite plugin. Each stub re-exports from
 * `globalThis.__bifrost_<key>`, which `initReactShim()` populates at boot
 * with the host SPA's already-loaded copies of the same modules. This
 * avoids shipping a second copy of React in every app bundle and avoids
 * the "two Reacts" hooks failure.
 *
 * Adding a new platform-shared module:
 *   1. Add an entry below.
 *   2. Add a matching `globalThis.__bifrost_<key> = <module>` in
 *      `esm-react-shim.ts`.
 *   3. Add the key to the static `<script type="importmap">` in
 *      `client/index.html`.
 */

export interface PlatformModule {
	/**
	 * The bare specifier that app bundles import (e.g. "react",
	 * "react/jsx-runtime", "lucide-react").
	 */
	specifier: string;
	/**
	 * The globalThis key that holds the host SPA's copy of this module.
	 * Read by the stub at `/__bifrost_modules/<fileName>`.
	 */
	globalKey: string;
}

export const PLATFORM_MODULES: PlatformModule[] = [
	{ specifier: "react", globalKey: "__bifrost_react" },
	{ specifier: "react-dom", globalKey: "__bifrost_react_dom" },
	{ specifier: "react-dom/client", globalKey: "__bifrost_react_dom_client" },
	{ specifier: "react/jsx-runtime", globalKey: "__bifrost_react_jsx_runtime" },
	{ specifier: "react/jsx-dev-runtime", globalKey: "__bifrost_react_jsx_dev_runtime" },
	{ specifier: "react-router-dom", globalKey: "__bifrost_react_router_dom" },
	{ specifier: "lucide-react", globalKey: "__bifrost_lucide_react" },
];

/**
 * Map a bare specifier to the static stub URL the import map resolves it to.
 * `react/jsx-runtime` -> `/__bifrost_modules/react-jsx-runtime.js`.
 */
export function stubUrlFor(specifier: string): string {
	const fileName = specifier.replace(/\//g, "-").replace(/^@/, "") + ".js";
	return `/__bifrost_modules/${fileName}`;
}
