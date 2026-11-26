/**
 * App Builder Runtime Store
 *
 * Zustand store for managing App Builder application runtime state:
 * - Variables: User-defined state that can be set by actions
 * - Data: Loaded data from data sources (tables, workflows, etc.)
 * - Actions: Pending workflow executions and their results
 * - Navigation: Current page and route state
 */

import { isAppFrame } from "@/lib/auth-token";
import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

/**
 * Workflow execution state
 */
interface WorkflowExecution {
	workflowId: string;
	status: "pending" | "running" | "success" | "error";
	input?: Record<string, unknown>;
	output?: unknown;
	error?: string;
	startedAt: number;
	completedAt?: number;
}

/**
 * Data source state
 */
interface DataSourceState {
	data: unknown;
	isLoading: boolean;
	error?: string;
	lastFetched?: number;
}

/**
 * Table cache entry for persisting data across page navigations
 */
interface TableCacheEntry {
	data: unknown[];
	dataSourceKey: string;
	cachedAt: number;
}

/**
 * App Builder runtime state
 */
interface AppBuilderState {
	// App context (for navigation URL construction)
	appSlug: string | null;
	isPreview: boolean;
	setAppContext: (slug: string, isPreview: boolean) => void;
	getBasePath: () => string;

	// Variables
	variables: Record<string, unknown>;
	setVariable: (name: string, value: unknown) => void;
	setVariables: (vars: Record<string, unknown>) => void;
	clearVariables: () => void;

	// Data sources
	dataSources: Record<string, DataSourceState>;
	setDataSource: (key: string, data: unknown) => void;
	setDataSourceLoading: (key: string, isLoading: boolean) => void;
	setDataSourceError: (key: string, error: string) => void;
	clearDataSources: () => void;
	refreshDataSource: (key: string) => void;

	// Workflow executions
	executions: Record<string, WorkflowExecution>;
	startExecution: (
		executionId: string,
		workflowId: string,
		input?: Record<string, unknown>,
	) => void;
	completeExecution: (executionId: string, output: unknown) => void;
	failExecution: (executionId: string, error: string) => void;
	clearExecutions: () => void;

	// Navigation state (managed separately from router for internal state)
	currentPageId: string | null;
	setCurrentPageId: (pageId: string | null) => void;

	// Selected table rows (for bulk actions)
	selectedRows: Record<string, Set<string>>;
	selectRow: (tableId: string, rowId: string) => void;
	deselectRow: (tableId: string, rowId: string) => void;
	selectAllRows: (tableId: string, rowIds: string[]) => void;
	clearSelectedRows: (tableId: string) => void;
	clearAllSelectedRows: () => void;

	// Table data cache (persists across page navigations)
	tableCache: Record<string, TableCacheEntry>;
	setTableCache: (
		cacheKey: string,
		data: unknown[],
		dataSourceKey: string,
	) => void;
	getTableCache: (cacheKey: string) => TableCacheEntry | undefined;
	clearTableCache: (cacheKey: string) => void;
	clearAllTableCache: () => void;

	// Reset all state
	reset: () => void;
}

/**
 * Initial state values
 */
const initialState = {
	appSlug: null as string | null,
	isPreview: false,
	variables: {},
	dataSources: {},
	executions: {},
	currentPageId: null,
	selectedRows: {},
	tableCache: {},
};

/**
 * App Builder runtime store
 *
 * Manages all runtime state for App Builder applications including:
 * - User-defined variables that persist during the session
 * - Data loaded from various sources
 * - Workflow execution tracking
 * - Table row selection state
 */
export const useAppBuilderStore = create<AppBuilderState>()(
	subscribeWithSelector((set, get) => ({
		// Initial state
		...initialState,

		// App context management
		setAppContext: (slug, isPreview) => set({ appSlug: slug, isPreview }),
		getBasePath: () => {
			const { appSlug, isPreview } = get();
			if (!appSlug) return "/apps";
			return isPreview
				? `/apps/${appSlug}/preview`
				: `${isAppFrame() ? "/embedded" : ""}/apps/${appSlug}`;
		},

		// Variable management
		setVariable: (name, value) =>
			set((state) => ({
				variables: { ...state.variables, [name]: value },
			})),

		setVariables: (vars) =>
			set((state) => ({
				variables: { ...state.variables, ...vars },
			})),

		clearVariables: () => set({ variables: {} }),

		// Data source management
		setDataSource: (key, data) =>
			set((state) => ({
				dataSources: {
					...state.dataSources,
					[key]: {
						data,
						isLoading: false,
						lastFetched: Date.now(),
					},
				},
			})),

		setDataSourceLoading: (key, isLoading) =>
			set((state) => ({
				dataSources: {
					...state.dataSources,
					[key]: {
						...state.dataSources[key],
						data: state.dataSources[key]?.data,
						isLoading,
					},
				},
			})),

		setDataSourceError: (key, error) =>
			set((state) => ({
				dataSources: {
					...state.dataSources,
					[key]: {
						...state.dataSources[key],
						data: state.dataSources[key]?.data,
						isLoading: false,
						error,
					},
				},
			})),

		clearDataSources: () => set({ dataSources: {} }),

		refreshDataSource: (key) => {
			// Mark as needing refresh - the actual data fetching
			// is handled by the component that owns the data source
			const { dataSources } = get();
			if (dataSources[key]) {
				set({
					dataSources: {
						...dataSources,
						[key]: {
							...dataSources[key],
							lastFetched: undefined, // Clear to trigger refetch
						},
					},
				});
			}
		},

		// Workflow execution management
		startExecution: (executionId, workflowId, input) =>
			set((state) => ({
				executions: {
					...state.executions,
					[executionId]: {
						workflowId,
						status: "running",
						input,
						startedAt: Date.now(),
					},
				},
			})),

		completeExecution: (executionId, output) =>
			set((state) => {
				const execution = state.executions[executionId];
				if (!execution) return state;

				return {
					executions: {
						...state.executions,
						[executionId]: {
							...execution,
							status: "success",
							output,
							completedAt: Date.now(),
						},
					},
				};
			}),

		failExecution: (executionId, error) =>
			set((state) => {
				const execution = state.executions[executionId];
				if (!execution) return state;

				return {
					executions: {
						...state.executions,
						[executionId]: {
							...execution,
							status: "error",
							error,
							completedAt: Date.now(),
						},
					},
				};
			}),

		clearExecutions: () => set({ executions: {} }),

		// Navigation state
		setCurrentPageId: (pageId) => set({ currentPageId: pageId }),

		// Table row selection
		selectRow: (tableId, rowId) =>
			set((state) => {
				const currentSelection =
					state.selectedRows[tableId] ?? new Set<string>();
				const newSelection = new Set(currentSelection);
				newSelection.add(rowId);
				return {
					selectedRows: {
						...state.selectedRows,
						[tableId]: newSelection,
					},
				};
			}),

		deselectRow: (tableId, rowId) =>
			set((state) => {
				const currentSelection = state.selectedRows[tableId];
				if (!currentSelection) return state;

				const newSelection = new Set(currentSelection);
				newSelection.delete(rowId);
				return {
					selectedRows: {
						...state.selectedRows,
						[tableId]: newSelection,
					},
				};
			}),

		selectAllRows: (tableId, rowIds) =>
			set((state) => ({
				selectedRows: {
					...state.selectedRows,
					[tableId]: new Set(rowIds),
				},
			})),

		clearSelectedRows: (tableId) =>
			set((state) => ({
				selectedRows: {
					...state.selectedRows,
					[tableId]: new Set<string>(),
				},
			})),

		clearAllSelectedRows: () => set({ selectedRows: {} }),

		// Table data cache management
		setTableCache: (cacheKey, data, dataSourceKey) =>
			set((state) => ({
				tableCache: {
					...state.tableCache,
					[cacheKey]: {
						data,
						dataSourceKey,
						cachedAt: Date.now(),
					},
				},
			})),

		getTableCache: (cacheKey) => {
			return get().tableCache[cacheKey];
		},

		clearTableCache: (cacheKey) =>
			set((state) => {
				const { [cacheKey]: _, ...rest } = state.tableCache;
				return { tableCache: rest };
			}),

		clearAllTableCache: () => set({ tableCache: {} }),

		// Reset all state
		reset: () => set(initialState),
	})),
);

/**
 * Selector hooks for specific state slices
 */

/** Get a specific variable value */
export const useAppVariable = <T = unknown>(name: string): T | undefined => {
	return useAppBuilderStore((state) => state.variables[name]) as
		| T
		| undefined;
};

/** Get a specific data source */
export const useAppDataSource = (key: string): DataSourceState | undefined => {
	return useAppBuilderStore((state) => state.dataSources[key]);
};

/** Get a specific execution */
export const useAppExecution = (
	executionId: string,
): WorkflowExecution | undefined => {
	return useAppBuilderStore((state) => state.executions[executionId]);
};

/** Check if any workflow is currently executing */
export const useIsAnyWorkflowExecuting = (): boolean => {
	return useAppBuilderStore((state) =>
		Object.values(state.executions).some((e) => e.status === "running"),
	);
};

/** Get selected rows for a specific table */
export const useAppSelectedRows = (tableId: string): Set<string> => {
	return useAppBuilderStore(
		(state) => state.selectedRows[tableId] ?? new Set(),
	);
};

/** Get table cache entry */
export const useTableCache = (
	cacheKey: string | undefined,
): TableCacheEntry | undefined => {
	return useAppBuilderStore((state) =>
		cacheKey ? state.tableCache[cacheKey] : undefined,
	);
};
