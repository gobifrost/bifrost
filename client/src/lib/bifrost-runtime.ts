/**
 * Bifrost runtime surface for bundled apps.
 *
 * This module re-exports everything that user code can `import { X } from "bifrost"`.
 * BundledAppShell installs an import map entry pointing `"bifrost"` at a blob module
 * that re-exports all of these — so the bundler treats `"bifrost"` as an external
 * and the browser resolves it to this host-loaded module at runtime.
 *
 * Anything exported here is part of the public `bifrost` package surface.
 */

// React primitives — re-exported so `import { useState, useEffect } from "bifrost"` works.
export {
	default as React,
	Fragment,
	Suspense,
	lazy,
	memo,
	forwardRef,
	createContext,
	useCallback,
	useContext,
	useDebugValue,
	useDeferredValue,
	useEffect,
	useId,
	useImperativeHandle,
	useInsertionEffect,
	useLayoutEffect,
	useMemo,
	useReducer,
	useRef,
	useState,
	useSyncExternalStore,
	useTransition,
} from "react";

// React Router primitives EXCEPT Link / NavLink / Navigate / useNavigate /
// useLocation — those are re-exported from the platform module below so app
// basename handling stays consistent during the transition.
export {
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
	useMatch,
	useNavigationType,
	useOutlet,
	useOutletContext,
	useResolvedPath,
	useRoutes,
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

// Lucide icons — wildcard re-export so any icon name is available from "bifrost".
// Migrated apps import from "lucide-react" directly; this keeps un-migrated apps
// working until the final cleanup.
//
// Four icon names collide with shadcn UI component exports below: Badge, Sheet,
// Table, Command. Platform wins on collision — the shadcn components are what
// user code meant when it wrote `import { Badge } from "bifrost"`. Those four
// shadcn modules are re-exported with explicit named re-exports (not `export *`)
// further down, so TypeScript treats them as overrides of this wildcard rather
// than ambiguous duplicates.
export * from "lucide-react";

// Platform hooks & components
export { useWorkflowQuery } from "./app-code-platform/useWorkflowQuery";
export { useWorkflowMutation } from "./app-code-platform/useWorkflowMutation";
export { useLocation } from "./app-code-platform/useLocation";
export { useParams } from "./app-code-platform/useParams";
export { useSearchParams } from "./app-code-platform/useSearchParams";
export { navigate, useNavigate } from "./app-code-platform/navigate";
export { useUser } from "./app-code-platform/useUser";
export { RequireRole } from "./app-code-platform/RequireRole";
export { useAppState } from "./app-code-platform/useAppState";
export { Link, NavLink, Navigate } from "./app-code-platform/navigation";
export { tables } from "./app-sdk/tables";
export { useTable } from "./app-sdk/use-table";
export { useInfiniteTable } from "./app-sdk/use-infinite-table";
export { files } from "./app-sdk/files";
export { useFiles } from "./app-sdk/use-files";

// Utilities
export * from "./utils";
export { clsx } from "clsx";
export { twMerge } from "tailwind-merge";
export { format } from "date-fns";

// shadcn UI components
export * from "@/components/ui/button";
export * from "@/components/ui/input";
export * from "@/components/ui/label";
export * from "@/components/ui/textarea";
export * from "@/components/ui/card";
// Explicit re-export: Badge collides with lucide-react's Badge icon. Platform wins.
export { Badge, badgeVariants } from "@/components/ui/badge";
export * from "@/components/ui/avatar";
export * from "@/components/ui/checkbox";
export * from "@/components/ui/switch";
export * from "@/components/ui/select";
// Explicit re-export: Table collides with lucide-react's Table icon. Platform wins.
export {
	Table,
	TableHeader,
	TableBody,
	TableFooter,
	TableHead,
	TableRow,
	TableCell,
	TableCaption,
} from "@/components/ui/table";
export * from "@/components/ui/tabs";
export * from "@/components/ui/dialog";
export * from "@/components/ui/dropdown-menu";
export * from "@/components/ui/tooltip";
export * from "@/components/ui/progress";
export * from "@/components/ui/skeleton";
export * from "@/components/ui/alert";
export * from "@/components/ui/accordion";
export * from "@/components/ui/collapsible";
export * from "@/components/ui/popover";
export * from "@/components/ui/radio-group";
export * from "@/components/ui/slider";
export * from "@/components/ui/toggle";
export * from "@/components/ui/toggle-group";
export * from "@/components/ui/hover-card";
// Explicit re-export: Command collides with lucide-react's Command icon. Platform wins.
export {
	Command,
	CommandDialog,
	CommandInput,
	CommandList,
	CommandEmpty,
	CommandGroup,
	CommandItem,
	CommandShortcut,
	CommandSeparator,
} from "@/components/ui/command";
export * from "@/components/ui/alert-dialog";
export * from "@/components/ui/context-menu";
// Explicit re-export: Sheet collides with lucide-react's Sheet icon. Platform wins.
export {
	Sheet,
	SheetTrigger,
	SheetClose,
	SheetContent,
	SheetHeader,
	SheetFooter,
	SheetTitle,
	SheetDescription,
} from "@/components/ui/sheet";
export * from "@/components/ui/separator";
export * from "@/components/ui/combobox";
export * from "@/components/ui/pagination";
export * from "@/components/ui/tags-input";
export * from "@/components/ui/multi-combobox";
export { Calendar as CalendarPicker, CalendarDayButton } from "@/components/ui/calendar";
export * from "@/components/ui/date-range-picker";
