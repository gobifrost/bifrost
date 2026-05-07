/**
 * App Code Runtime
 *
 * Creates React components from compiled code by injecting
 * the platform scope (React hooks, platform APIs, UI components).
 *
 * Architecture:
 * - User code imports from "bifrost": `import { Button, cn } from "bifrost"`
 * - Compiler transforms to: `const { Button, cn } = $;`
 * - Runtime passes `$` containing everything
 * - No manual maintenance of what's "available" - just add to the registry
 */

import React from "react";
import {
	// Exclude: Link, NavLink, Navigate, useNavigate (wrapped in platform scope)
	// Include everything else from react-router-dom
	BrowserRouter,
	HashRouter,
	MemoryRouter,
	Router,
	RouterProvider,
	Routes,
	Route,
	Outlet,
	useHref,
	useLinkClickHandler,
	useInRouterContext,
	useLocation,
	useMatch,
	useNavigationType,
	useOutlet,
	useOutletContext,
	useParams,
	useResolvedPath,
	useRoutes,
	useSearchParams,
	createBrowserRouter,
	createHashRouter,
	createMemoryRouter,
	createRoutesFromChildren,
	createRoutesFromElements,
	createSearchParams,
	generatePath,
	matchPath,
	matchRoutes,
	renderMatches,
	resolvePath,
	ScrollRestoration,
	useBeforeUnload,
	useFetcher,
	useFetchers,
	useLoaderData,
	useNavigation,
	useRevalidator,
	useRouteError,
	useRouteLoaderData,
	useSubmit,
	useBlocker,
	unstable_usePrompt,
	Form,
	Await,
	useActionData,
	useAsyncError,
	useAsyncValue,
	UNSAFE_DataRouterContext,
	UNSAFE_DataRouterStateContext,
	UNSAFE_NavigationContext,
	UNSAFE_LocationContext,
	UNSAFE_RouteContext,
} from "react-router-dom";
import * as LucideIcons from "lucide-react";
/**
 * Wrap compiled code in a component factory.
 */
function wrapAsComponent(compiled: string): string {
	return `
    var __defaultExport__;
    var __exports__ = {};
    ${compiled}
    return __defaultExport__;
  `;
}
import { createPlatformScope } from "./app-code-platform/scope";

/**
 * React Router exports that we include in the runtime.
 * Note: Link, NavLink, Navigate, useNavigate are EXCLUDED here because
 * they are provided by the platform scope with path transformation.
 */
const reactRouterExports = {
	BrowserRouter,
	HashRouter,
	MemoryRouter,
	Router,
	RouterProvider,
	Routes,
	Route,
	Outlet,
	useHref,
	useLinkClickHandler,
	useInRouterContext,
	useLocation,
	useMatch,
	useNavigationType,
	useOutlet,
	useOutletContext,
	useParams,
	useResolvedPath,
	useRoutes,
	useSearchParams,
	createBrowserRouter,
	createHashRouter,
	createMemoryRouter,
	createRoutesFromChildren,
	createRoutesFromElements,
	createSearchParams,
	generatePath,
	matchPath,
	matchRoutes,
	renderMatches,
	resolvePath,
	ScrollRestoration,
	useBeforeUnload,
	useFetcher,
	useFetchers,
	useLoaderData,
	useNavigation,
	useRevalidator,
	useRouteError,
	useRouteLoaderData,
	useSubmit,
	useBlocker,
	unstable_usePrompt,
	Form,
	Await,
	useActionData,
	useAsyncError,
	useAsyncValue,
	UNSAFE_DataRouterContext,
	UNSAFE_DataRouterStateContext,
	UNSAFE_NavigationContext,
	UNSAFE_LocationContext,
	UNSAFE_RouteContext,
};

// Utilities
import * as utils from "./utils";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// UI Components - import entire modules
import * as ButtonModule from "@/components/ui/button";
import * as InputModule from "@/components/ui/input";
import * as LabelModule from "@/components/ui/label";
import * as TextareaModule from "@/components/ui/textarea";
import * as CardModule from "@/components/ui/card";
import * as BadgeModule from "@/components/ui/badge";
import * as AvatarModule from "@/components/ui/avatar";
import * as CheckboxModule from "@/components/ui/checkbox";
import * as SwitchModule from "@/components/ui/switch";
import * as SelectModule from "@/components/ui/select";
import * as TableModule from "@/components/ui/table";
import * as TabsModule from "@/components/ui/tabs";
import * as DialogModule from "@/components/ui/dialog";
import * as DropdownMenuModule from "@/components/ui/dropdown-menu";
import * as TooltipModule from "@/components/ui/tooltip";
import * as ProgressModule from "@/components/ui/progress";
import * as SkeletonModule from "@/components/ui/skeleton";
import * as AlertModule from "@/components/ui/alert";
import * as AccordionModule from "@/components/ui/accordion";
import * as CollapsibleModule from "@/components/ui/collapsible";
import * as PopoverModule from "@/components/ui/popover";
import * as RadioGroupModule from "@/components/ui/radio-group";
import * as SliderModule from "@/components/ui/slider";
import * as ToggleModule from "@/components/ui/toggle";
import * as ToggleGroupModule from "@/components/ui/toggle-group";
import * as HoverCardModule from "@/components/ui/hover-card";
import * as CommandModule from "@/components/ui/command";
import * as AlertDialogModule from "@/components/ui/alert-dialog";
import * as ContextMenuModule from "@/components/ui/context-menu";
import * as SheetModule from "@/components/ui/sheet";
import * as SeparatorModule from "@/components/ui/separator";
import * as ComboboxModule from "@/components/ui/combobox";
import * as PaginationModule from "@/components/ui/pagination";
import * as TagsInputModule from "@/components/ui/tags-input";
import * as MultiComboboxModule from "@/components/ui/multi-combobox";
import { Calendar as CalendarPicker, CalendarDayButton } from "@/components/ui/calendar";
import * as DateRangePickerModule from "@/components/ui/date-range-picker";

// Utilities - date-fns
import { format } from "date-fns";

// Toast notifications via Sonner — <Toaster /> is mounted in main.tsx.
import { toast } from "sonner";

/**
 * The $ registry - contains EVERYTHING available to user code
 *
 * Users import from "bifrost" and the compiler transforms to destructuring from $.
 * This means we just need to add things here - no manual maintenance elsewhere.
 *
 * IMPORTANT: Platform scope is spread AFTER React Router exports, so wrapped
 * navigation components (Link, NavLink, Navigate, useNavigate) from the platform
 * take precedence over the raw React Router versions.
 */
export const $: Record<string, unknown> = {
	// React core and all hooks
	React,
	...React,

	// React Router - selective exports (excludes Link, NavLink, Navigate, useNavigate)
	...reactRouterExports,

	// Lucide icons - all of them
	// MUST come before platform scope so Link/NavLink/Navigate navigation components
	// override Lucide's Link icon
	...LucideIcons,

	// Re-assert global built-ins. Lucide ships icons named Map, Set, Type, Filter,
	// Form, etc. — without this, `new Map()` in user code resolves to the Map
	// *icon* component and crashes with "Map2 is not a constructor".
	Map: globalThis.Map,
	Set: globalThis.Set,
	WeakMap: globalThis.WeakMap,
	WeakSet: globalThis.WeakSet,
	Date: globalThis.Date,

	// Platform APIs (useWorkflowQuery, useWorkflowMutation, navigate, useUser, etc.)
	// MUST come after Lucide icons to override Link, NavLink, Navigate, useNavigate
	...createPlatformScope(),

	// Utilities
	...utils,
	clsx,
	twMerge,

	// UI Components - spread all exports from each module
	...ButtonModule,
	...InputModule,
	...LabelModule,
	...TextareaModule,
	...CardModule,
	...BadgeModule,
	...AvatarModule,
	...CheckboxModule,
	...SwitchModule,
	...SelectModule,
	...TableModule,
	...TabsModule,
	...DialogModule,
	...DropdownMenuModule,
	...TooltipModule,
	...ProgressModule,
	...SkeletonModule,
	...AlertModule,
	...AccordionModule,
	...CollapsibleModule,
	...PopoverModule,
	...RadioGroupModule,
	...SliderModule,
	...ToggleModule,
	...ToggleGroupModule,
	...HoverCardModule,
	...CommandModule,
	...AlertDialogModule,
	...ContextMenuModule,
	...SheetModule,
	...SeparatorModule,
	...ComboboxModule,
	...PaginationModule,
	...TagsInputModule,
	...MultiComboboxModule,
	CalendarPicker,
	CalendarDayButton,
	...DateRangePickerModule,

	// Utilities - date-fns
	format,

	// Toast notifications (Sonner)
	toast,
};

/**
 * Error component displayed when compilation or runtime errors occur
 * Renders as plain text to blend with editor content
 */
function ErrorComponent({
	title,
	message,
}: {
	title: string;
	message: string;
}): React.ReactElement {
	return React.createElement(
		"div",
		{
			className: "p-4 text-destructive",
		},
		React.createElement(
			"div",
			{ className: "font-semibold mb-2" },
			title,
		),
		React.createElement(
			"pre",
			{
				className: "text-sm whitespace-pre-wrap font-mono overflow-auto",
			},
			message,
		),
	);
}

/**
 * Create a React component from source or pre-compiled code
 *
 * This function:
 * 1. Compiles the source (if not already compiled)
 * 2. Wraps it as a component factory
 * 3. Creates a function with $ injected
 * 4. Returns the resulting React component
 *
 * @param source - Source code or pre-compiled JavaScript
 * @param customComponents - Additional components to inject (e.g., app-specific components)
 * @param useCompiled - If true, source is already compiled and doesn't need transformation
 * @param externalDeps - External npm dependencies loaded from esm.sh (injected as $deps)
 * @returns A React component that renders the code
 *
 * @example
 * ```typescript
 * // From source
 * const MyPage = createComponent(`
 *   import { useWorkflowQuery, Button } from "bifrost";
 *   const { data, isLoading } = useWorkflowQuery('get_clients');
 *   if (isLoading) return <div>Loading...</div>;
 *   return <Button>{data.length} clients</Button>;
 * `);
 *
 * // From pre-compiled (for production)
 * const MyPage = createComponent(compiledCode, {}, true);
 *
 * // With custom components
 * const MyPage = createComponent(source, { ClientCard, DataGrid });
 * ```
 */
export function createComponent(
	source: string,
	customComponents: Record<string, React.ComponentType> = {},
	useCompiled: boolean = false,
	externalDeps: Record<string, Record<string, unknown>> = {},
): React.ComponentType {
	// Step 1: Compile if needed
	let compiled: string;

	if (useCompiled) {
		compiled = source;
	} else {
		// Server-side compilation required — if we reach here without
		// compiled code, something went wrong in the pipeline
		return function ServerCompileRequired() {
			return ErrorComponent({
				title: "Compilation Required",
				message: "This file needs server-side compilation. Save the file to trigger compilation.",
			});
		};
	}

	// Step 2: Build the full scope
	const scope = {
		...$,
		...customComponents,
		$: { ...$, ...customComponents }, // Also provide $ for explicit imports
		$deps: externalDeps,
	};

	// Step 3: Create argument names and values for the function
	// Everything is available as direct variables (for code without imports)
	// AND as properties on $ (for `var { X } = $;` from compiled imports)
	// The compiler uses `var` (not `const`) so redeclarations don't error.
	const argNames = Object.keys(scope);
	const argValues = Object.values(scope);

	// Step 4: Wrap the compiled code as a component factory
	const wrapped = wrapAsComponent(compiled);

	// Step 5: Create and execute the factory
	try {
		// Create a function that takes all scope items as arguments
		const factory = new Function(...argNames, wrapped) as (
			...args: unknown[]
		) => React.ComponentType;

		// Execute the factory with our scope values
		const Component = factory(...argValues);

		// Wrap in an error boundary function component
		return function SafeComponent(
			props: Record<string, unknown>,
		): React.ReactElement {
			try {
				return React.createElement(Component, props);
			} catch (err) {
				const errorMessage =
					err instanceof Error ? err.message : "Unknown runtime error";
				return ErrorComponent({
					title: "Runtime Error",
					message: errorMessage,
				});
			}
		};
	} catch (err) {
		// Factory creation or execution failed
		const errorMessage =
			err instanceof Error ? err.message : "Unknown error creating component";
		return function FactoryError() {
			return ErrorComponent({
				title: "Component Factory Error",
				message: errorMessage,
			});
		};
	}
}
