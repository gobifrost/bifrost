/**
 * App Code Platform Type Definitions
 *
 * This file provides TypeScript type definitions for the App Builder platform.
 * These types are used by the Monaco editor to provide IntelliSense support
 * when users write components in the App Builder.
 *
 * All types defined here correspond to the actual runtime implementations
 * in the app-code-platform/ directory.
 */

// =============================================================================
// Platform Hooks
// =============================================================================

/**
 * Streaming log entry from workflow execution
 */
export interface StreamingLog {
	/** Log level (DEBUG, INFO, WARNING, ERROR, etc.) */
	level: string;
	/** Log message content */
	message: string;
	/** ISO timestamp of when the log was created */
	timestamp: string;
	/** Optional sequence number for ordering */
	sequence?: number;
}

/**
 * Execution status values
 */
export type ExecutionStatus =
	| "Pending"
	| "Running"
	| "Success"
	| "Failed"
	| "CompletedWithErrors"
	| "Timeout"
	| "Cancelled"
	| "Cancelling";

/**
 * Options for useWorkflowQuery
 */
export interface UseWorkflowQueryOptions {
	/** Whether to execute automatically. Default: true. Set false to skip execution until enabled. */
	enabled?: boolean;
}

/**
 * Result object returned by useWorkflowQuery
 * @template T - The type of data returned by the workflow
 */
export interface UseWorkflowQueryResult<T> {
	/** The workflow result data. Null until completed. */
	data: T | null;
	/** True while the workflow is executing. */
	isLoading: boolean;
	/** True if the workflow failed. */
	isError: boolean;
	/**
	 * Error message string if the workflow failed, otherwise null.
	 * Already a string — do NOT access `.message` on this value.
	 */
	errorMessage: string | null;
	/**
	 * @deprecated Use `errorMessage` — this is a string alias kept for
	 * backward compatibility. Reading `error.message` returns undefined.
	 */
	error: string | null;
	/** Streaming logs array that updates in real-time. */
	logs: StreamingLog[];
	/** Re-execute the workflow with the same params. */
	refetch: () => Promise<T>;
	/** Current execution ID. */
	executionId: string | null;
	/** Current execution status. */
	status: ExecutionStatus | null;
}

/**
 * Declarative workflow data fetching hook.
 * Executes automatically on mount, re-executes when params change.
 *
 * @template T - The type of data returned by the workflow
 * @param workflowId - The workflow ID to execute
 * @param params - Optional parameters passed to the workflow
 * @param options - Options like `enabled` to control execution
 * @returns Object with data, loading state, error, and refetch
 *
 * @example
 * ```tsx
 * // Load data on mount
 * const { data, isLoading, errorMessage } = useWorkflowQuery<Customer[]>(
 *   "workflow-uuid",
 *   { limit: 10 }
 * );
 *
 * if (isLoading) return <Skeleton />;
 * if (errorMessage) return <Alert>{errorMessage}</Alert>;
 * return <CustomerList data={data} />;
 * ```
 *
 * @example
 * ```tsx
 * // Conditional execution
 * const params = useParams();
 * const { data } = useWorkflowQuery("get-customer", { id: params.id }, {
 *   enabled: !!params.id
 * });
 * ```
 */
export declare function useWorkflowQuery<T = unknown>(
	workflowId: string,
	params?: Record<string, unknown>,
	options?: UseWorkflowQueryOptions,
): UseWorkflowQueryResult<T>;

/**
 * Result object returned by useWorkflowMutation
 * @template T - The type of data returned by the workflow
 */
export interface UseWorkflowMutationResult<T> {
	/** Execute the workflow. Returns a Promise that resolves to the result. */
	execute: (params?: Record<string, unknown>) => Promise<T>;
	/** True while the workflow is executing. */
	isLoading: boolean;
	/** True if the last execution failed. */
	isError: boolean;
	/**
	 * Error message string from the last execution, otherwise null.
	 * Already a string — do NOT access `.message` on this value.
	 */
	errorMessage: string | null;
	/**
	 * @deprecated Use `errorMessage` — this is a string alias kept for
	 * backward compatibility. Reading `error.message` returns undefined.
	 */
	error: string | null;
	/** Result data from the last execution. */
	data: T | null;
	/** Streaming logs array that updates in real-time. */
	logs: StreamingLog[];
	/** Reset data, error, and loading state. */
	reset: () => void;
	/** Current execution ID. */
	executionId: string | null;
	/** Current execution status. */
	status: ExecutionStatus | null;
}

/**
 * Imperative workflow execution hook.
 * Does nothing until execute() is called. Returns result as Promise<T>.
 *
 * @template T - The type of data returned by the workflow
 * @param workflowId - The workflow ID to execute
 * @returns Object with execute function and reactive state
 *
 * @example
 * ```tsx
 * // Button-triggered action
 * const { execute, isLoading } = useWorkflowMutation("workflow-uuid");
 *
 * const handleCreate = async () => {
 *   try {
 *     const result = await execute({ name: "Acme" });
 *     setItems(prev => [...prev, result.item]);
 *   } catch (err) {
 *     console.error("Failed:", err.message);
 *   }
 * };
 * ```
 *
 * @example
 * ```tsx
 * // Sequential/batch calls
 * const { execute } = useWorkflowMutation("workflow-uuid");
 *
 * const handleBatch = async () => {
 *   for (const item of items) {
 *     const result = await execute({ id: item.id });
 *     results.push(result);
 *   }
 * };
 * ```
 */
export declare function useWorkflowMutation<T = unknown>(
	workflowId: string,
): UseWorkflowMutationResult<T>;

/**
 * Current user information available in apps
 */
export interface AppCodeUser {
	/** User's unique identifier */
	id: string;

	/** User's email address */
	email: string;

	/** User's display name */
	name: string;

	/**
	 * All roles assigned to the user.
	 * Use hasRole() for role checking.
	 */
	roles: string[];

	/**
	 * Check if user has a specific role.
	 * @param role - The role to check for
	 * @returns true if the user has the specified role
	 */
	hasRole: (role: string) => boolean;

	/**
	 * User's organization ID.
	 * Empty string for platform users who aren't associated with an organization.
	 */
	organizationId: string;
}

/**
 * Hook to get the current authenticated user information.
 * Returns a consistent shape even if the user is not authenticated.
 *
 * @returns User object with id, email, name, roles, hasRole(), and organizationId
 *
 * @example
 * ```jsx
 * const user = useUser();
 *
 * return (
 *   <div>
 *     <Text>Welcome, {user.name}</Text>
 *     <Text muted>{user.email}</Text>
 *     {user.hasRole('Admin') && (
 *       <Button onClick={() => navigate('/settings')}>
 *         Settings
 *       </Button>
 *     )}
 *   </div>
 * );
 * ```
 */
export declare function useUser(): AppCodeUser;

/**
 * Hook to get URL path parameters from the current route.
 * Returns all dynamic segments defined in the route path.
 *
 * @returns Object containing all URL parameters as string values
 *
 * @example
 * ```jsx
 * // URL: /clients/123/contacts
 * // Route: /clients/:clientId/contacts
 *
 * const params = useParams();
 * // params = { clientId: "123" }
 *
 * const { data: client } = useWorkflowQuery('get_client', { id: params.clientId });
 * ```
 */
export declare function useParams(): Record<string, string>;

/**
 * Hook to get query string parameters from the current URL.
 * Returns a URLSearchParams object for accessing query parameters.
 *
 * @returns URLSearchParams object for accessing query parameters
 *
 * @example
 * ```jsx
 * // URL: /clients?status=active&page=2
 *
 * const searchParams = useSearchParams();
 *
 * const status = searchParams.get('status'); // "active"
 * const page = searchParams.get('page'); // "2"
 *
 * // Iterate over all params
 * for (const [key, value] of searchParams) {
 *   console.log(key, value);
 * }
 * ```
 */
export declare function useSearchParams(): URLSearchParams;

/**
 * Hook to get a navigation function for programmatic navigation.
 * Returns a stable function that navigates to the specified path.
 *
 * @returns A function that navigates to the specified path
 *
 * @example
 * ```jsx
 * const nav = useNavigate();
 *
 * return (
 *   <Button onClick={() => nav('/clients/new')}>
 *     Add Client
 *   </Button>
 * );
 * ```
 */
export declare function useNavigate(): (path: string) => void;

/**
 * Hook for cross-page app state that persists across page navigations.
 * Similar to useState but state is shared across all pages in the app.
 * State is reset when the app is closed or switched to a different app.
 *
 * @template T - The type of the state value
 * @param key - Unique key for the state value (must be unique within the app)
 * @param initialValue - Initial value if state is not already set
 * @returns Tuple of [value, setValue] similar to useState
 *
 * @example
 * ```jsx
 * // In any page or component
 * const [selectedClientId, setSelectedClientId] = useAppState('selectedClient', null);
 *
 * // Value persists when navigating to other pages
 * <Button onClick={() => {
 *   setSelectedClientId(client.id);
 *   navigate('/client-details');
 * }}>
 *   View Details
 * </Button>
 * ```
 *
 * @example
 * ```jsx
 * // Sharing state across pages
 *
 * // Page 1: Set the state
 * const [cart, setCart] = useAppState('cart', []);
 * setCart([...cart, newItem]);
 *
 * // Page 2: Read the same state
 * const [cart] = useAppState('cart', []);
 * // cart contains items added from Page 1
 * ```
 */
export declare function useAppState<T>(
	key: string,
	initialValue: T,
): [T, (value: T) => void];

// =============================================================================
// Table Access SDK
// =============================================================================

export declare const tables: typeof import("./app-sdk/tables").tables;

/**
 * Live-updating table data hook.
 *
 * Loads an initial snapshot and subscribes to live row changes (insert,
 * update, delete) for the given table. The subscribe filter is the same
 * `where` expression passed to the initial query, so the websocket fanout
 * sees exactly the same row visibility as the snapshot.
 *
 * @param name - Table name (or id) to query and subscribe to
 * @param query - Optional `where` / `page` / `pageSize` / order / scope query parameters
 * @returns `{ rows, total, totalPages, loading, error }`
 *
 * @example
 * ```tsx
 * const { rows, totalPages, loading, error } = useTable("tickets", {
 *   where: { status: "open" },
 *   page: 1,
 *   pageSize: 50,
 * });
 *
 * if (loading) return <Skeleton />;
 * if (error) return <Alert>{error.message}</Alert>;
 * return <TicketList rows={rows} />;
 * ```
 */
export declare const useTable: typeof import("./app-sdk/use-table").useTable;

// =============================================================================
// Platform Utilities
// =============================================================================

/**
 * Navigate to a page path programmatically.
 *
 * Note: This is an imperative function. For navigation within components,
 * prefer using the useNavigate hook which provides a stable callback.
 * This function is useful in async callbacks where hooks aren't available.
 *
 * @param path - The path to navigate to (relative to the app root)
 *
 * @example
 * ```jsx
 * // In a component, prefer useNavigate:
 * const nav = useNavigate();
 * <Button onClick={() => nav('/clients')}>Go</Button>
 *
 * // This imperative version works in async callbacks:
 * const handleSuccess = async () => {
 *   navigate('/clients'); // Imperative navigation after async work
 * };
 * ```
 */
export declare function navigate(path: string): void;

// =============================================================================
// React Hooks (Re-exported from React for convenience)
// =============================================================================

/**
 * These React hooks are available directly in code without imports.
 * They are the standard React hooks from the React library.
 */

export declare const useState: typeof import("react").useState;
export declare const useEffect: typeof import("react").useEffect;
export declare const useMemo: typeof import("react").useMemo;
export declare const useCallback: typeof import("react").useCallback;
export declare const useRef: typeof import("react").useRef;
export declare const useContext: typeof import("react").useContext;
export declare const useReducer: typeof import("react").useReducer;
export declare const useLayoutEffect: typeof import("react").useLayoutEffect;
export declare const useImperativeHandle: typeof import("react").useImperativeHandle;
export declare const useDebugValue: typeof import("react").useDebugValue;
export declare const useDeferredValue: typeof import("react").useDeferredValue;
export declare const useTransition: typeof import("react").useTransition;
export declare const useId: typeof import("react").useId;
export declare const useSyncExternalStore: typeof import("react").useSyncExternalStore;

// =============================================================================
// React Utilities (Re-exported from React for convenience)
// =============================================================================

/**
 * These React utilities are available directly in code without imports.
 */

export declare const React: typeof import("react");
export declare const Fragment: typeof import("react").Fragment;
export declare const createElement: typeof import("react").createElement;
export declare const createContext: typeof import("react").createContext;
export declare const forwardRef: typeof import("react").forwardRef;
export declare const memo: typeof import("react").memo;
export declare const lazy: typeof import("react").lazy;
export declare const Suspense: typeof import("react").Suspense;

// =============================================================================
// Router Hooks
// =============================================================================

/**
 * Hook to get the current location object.
 * Provides access to the current URL pathname and search params.
 *
 * @returns Location object with pathname, search, hash, state, and key
 *
 * @example
 * ```jsx
 * const location = useLocation();
 *
 * // Highlight nav item based on current path
 * const isActive = location.pathname === '/clients';
 *
 * // Check for query params
 * if (location.search.includes('?status=active')) {
 *   // ...
 * }
 * ```
 */
export declare function useLocation(): {
	pathname: string;
	search: string;
	hash: string;
	state: unknown;
	key: string;
};

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Utility function for merging Tailwind CSS classes with proper precedence.
 * Combines clsx and tailwind-merge for conditional class name building.
 *
 * @param inputs - Class values (strings, arrays, objects with boolean values)
 * @returns Merged class string
 *
 * @example
 * ```jsx
 * // Basic usage
 * <div className={cn("flex items-center", "gap-2")}>
 *
 * // Conditional classes
 * <div className={cn(
 *   "rounded-lg p-4",
 *   isActive && "bg-primary text-white",
 *   isDisabled && "opacity-50 cursor-not-allowed"
 * )}>
 *
 * // Object syntax
 * <div className={cn("base-class", {
 *   "active-class": isActive,
 *   "error-class": hasError,
 * })}>
 * ```
 */
export declare function cn(...inputs: (string | undefined | null | false | Record<string, boolean>)[]): string;

/**
 * Format a date/time string in the user's local timezone.
 *
 * @param dateString - ISO date string or Date object
 * @param options - Intl.DateTimeFormatOptions to customize the output
 * @returns Formatted date string
 *
 * @example
 * ```jsx
 * formatDate(task.createdAt)  // "Jan 15, 2025, 03:45:12 PM"
 * formatDate(task.createdAt, { dateStyle: 'short' })  // "1/15/25"
 * ```
 */
export declare function formatDate(dateString: string | Date, options?: Intl.DateTimeFormatOptions): string;

/**
 * Format a date/time string as a short date (no time).
 *
 * @param dateString - ISO date string or Date object
 * @returns Formatted date string (e.g., "Jan 15, 2025")
 */
export declare function formatDateShort(dateString: string | Date): string;

/**
 * Format a date/time string relative to now (e.g., "2 hours ago", "in 3 days").
 *
 * @param dateString - ISO date string or Date object
 * @returns Relative time string
 */
export declare function formatRelativeTime(dateString: string | Date): string;

/**
 * Format a number with thousand separators.
 *
 * @param num - Number to format
 * @returns Formatted string (e.g., "1,234,567")
 */
export declare function formatNumber(num: number): string;

/**
 * Format a cost value as currency.
 *
 * @param cost - Cost value as string or number (may be null)
 * @returns Formatted currency string (e.g., "$0.0012") or "N/A" if null
 */
export declare function formatCost(cost: string | number | null | undefined): string;

/**
 * Format duration in milliseconds to a human-readable string.
 *
 * @param ms - Duration in milliseconds (may be null)
 * @returns Formatted duration string (e.g., "1.23s" or "456ms")
 */
export declare function formatDuration(ms: number | null | undefined): string;

// =============================================================================
// Icons (Lucide React)
// =============================================================================

/**
 * Icon component type from lucide-react.
 * All icons accept these common props.
 */
type LucideIcon = import("lucide-react").LucideIcon;

// Navigation & Layout Icons
export declare const LayoutDashboard: LucideIcon;
export declare const Home: LucideIcon;
export declare const Menu: LucideIcon;
export declare const X: LucideIcon;
export declare const ChevronRight: LucideIcon;
export declare const ChevronLeft: LucideIcon;
export declare const ChevronDown: LucideIcon;
export declare const ChevronUp: LucideIcon;
export declare const ArrowLeft: LucideIcon;
export declare const ArrowRight: LucideIcon;
export declare const ArrowUp: LucideIcon;
export declare const ArrowDown: LucideIcon;
export declare const ExternalLink: LucideIcon;

// Action Icons
export declare const Plus: LucideIcon;
export declare const Minus: LucideIcon;
export declare const Pencil: LucideIcon;
export declare const Trash2: LucideIcon;
export declare const Save: LucideIcon;
export declare const Download: LucideIcon;
export declare const Upload: LucideIcon;
export declare const Copy: LucideIcon;
export declare const Check: LucideIcon;
export declare const RefreshCw: LucideIcon;
export declare const Settings: LucideIcon;
export declare const MoreHorizontal: LucideIcon;
export declare const MoreVertical: LucideIcon;

// Object Icons
export declare const Users: LucideIcon;
export declare const User: LucideIcon;
export declare const UserPlus: LucideIcon;
export declare const Building: LucideIcon;
export declare const Building2: LucideIcon;
export declare const FolderKanban: LucideIcon;
export declare const Folder: LucideIcon;
export declare const FolderOpen: LucideIcon;
export declare const File: LucideIcon;
export declare const FileText: LucideIcon;
export declare const Calendar: LucideIcon;
export declare const Clock: LucideIcon;
export declare const Mail: LucideIcon;
export declare const Phone: LucideIcon;
export declare const MapPin: LucideIcon;

// Status & Feedback Icons
export declare const CheckSquare: LucideIcon;
export declare const Square: LucideIcon;
export declare const CheckCircle: LucideIcon;
export declare const XCircle: LucideIcon;
export declare const AlertCircle: LucideIcon;
export declare const AlertTriangle: LucideIcon;
export declare const Info: LucideIcon;
export declare const HelpCircle: LucideIcon;
export declare const Loader2: LucideIcon;

// Data & Analytics Icons
export declare const DollarSign: LucideIcon;
export declare const TrendingUp: LucideIcon;
export declare const TrendingDown: LucideIcon;
export declare const BarChart: LucideIcon;
export declare const PieChart: LucideIcon;
export declare const Activity: LucideIcon;

// Search & Filter Icons
export declare const Search: LucideIcon;
export declare const Filter: LucideIcon;
export declare const SlidersHorizontal: LucideIcon;

// Communication Icons
export declare const MessageSquare: LucideIcon;
export declare const Send: LucideIcon;
export declare const Bell: LucideIcon;

// Misc Icons
export declare const Star: LucideIcon;
export declare const Heart: LucideIcon;
export declare const Bookmark: LucideIcon;
export declare const Tag: LucideIcon;
export declare const Hash: LucideIcon;
export declare const LinkIcon: LucideIcon;
export declare const Eye: LucideIcon;
export declare const EyeOff: LucideIcon;
export declare const Lock: LucideIcon;
export declare const Unlock: LucideIcon;
export declare const Shield: LucideIcon;
export declare const Zap: LucideIcon;
export declare const Globe: LucideIcon;
export declare const Sun: LucideIcon;
export declare const Moon: LucideIcon;

// =============================================================================
// Aggregate Types for Monaco Editor Integration
// =============================================================================

/**
 * Complete platform scope type for Monaco editor IntelliSense.
 * This interface describes all APIs available to code at runtime.
 */
export interface PlatformScope {
	// React core
	React: typeof import("react");

	// React hooks
	useState: typeof import("react").useState;
	useEffect: typeof import("react").useEffect;
	useMemo: typeof import("react").useMemo;
	useCallback: typeof import("react").useCallback;
	useRef: typeof import("react").useRef;
	useContext: typeof import("react").useContext;
	useReducer: typeof import("react").useReducer;
	useLayoutEffect: typeof import("react").useLayoutEffect;
	useImperativeHandle: typeof import("react").useImperativeHandle;
	useDebugValue: typeof import("react").useDebugValue;
	useDeferredValue: typeof import("react").useDeferredValue;
	useTransition: typeof import("react").useTransition;
	useId: typeof import("react").useId;
	useSyncExternalStore: typeof import("react").useSyncExternalStore;

	// React utilities
	Fragment: typeof import("react").Fragment;
	createElement: typeof import("react").createElement;
	createContext: typeof import("react").createContext;
	forwardRef: typeof import("react").forwardRef;
	memo: typeof import("react").memo;
	lazy: typeof import("react").lazy;
	Suspense: typeof import("react").Suspense;

	// Platform hooks
	useWorkflowQuery: typeof useWorkflowQuery;
	useWorkflowMutation: typeof useWorkflowMutation;
	useUser: typeof useUser;
	useParams: typeof useParams;
	useSearchParams: typeof useSearchParams;
	useNavigate: typeof useNavigate;
	useAppState: typeof useAppState;
	useLocation: typeof useLocation;

	// Table access
	tables: typeof import("./app-sdk/tables").tables;
	useTable: typeof import("./app-sdk/use-table").useTable;

	// Platform utilities
	navigate: typeof navigate;
	cn: typeof cn;
	formatDate: typeof formatDate;
	formatDateShort: typeof formatDateShort;
	formatRelativeTime: typeof formatRelativeTime;
	formatNumber: typeof formatNumber;
	formatCost: typeof formatCost;
	formatDuration: typeof formatDuration;

	// Icons (partial list - see full exports above)
	LayoutDashboard: LucideIcon;
	Home: LucideIcon;
	Users: LucideIcon;
	Plus: LucideIcon;
	// ... and many more icons
}

/**
 * Type definitions string for Monaco editor.
 * This can be used to register type definitions with Monaco's TypeScript support.
 *
 * @example
 * ```typescript
 * import { MONACO_TYPE_DEFINITIONS } from '@/lib/app-code-platform.d.ts';
 *
 * monaco.languages.typescript.typescriptDefaults.addExtraLib(
 *   MONACO_TYPE_DEFINITIONS,
 *   'app-code-platform.d.ts'
 * );
 * ```
 */
export declare const MONACO_TYPE_DEFINITIONS: string;
