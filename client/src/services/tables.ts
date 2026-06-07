/**
 * Tables Service using openapi-react-query pattern
 *
 * All mutations automatically invalidate relevant queries so components
 * reading from useTables() will re-render with fresh data.
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

// Re-export types from OpenAPI spec
export type TablePublic = components["schemas"]["TablePublic"];
export type TableCreate = components["schemas"]["TableCreate"];
export type TableUpdate = components["schemas"]["TableUpdate"];
export type TableListResponse = components["schemas"]["TableListResponse"];
export type DocumentPublic = components["schemas"]["DocumentPublic"];
export type DocumentCreate = components["schemas"]["DocumentCreate"];
export type DocumentUpdate = components["schemas"]["DocumentUpdate"];
export type DocumentQuery = components["schemas"]["DocumentQuery"];
export type DocumentListResponse =
	components["schemas"]["DocumentListResponse"];
export type DocumentCountResponse =
	components["schemas"]["DocumentCountResponse"];
export type TablePolicies = components["schemas"]["TablePolicies"];
export type PolicyValidationResponse =
	components["schemas"]["PolicyValidationResponse"];
export type PolicyValidationError =
	components["schemas"]["PolicyValidationError"];

// Query filter operators (JSON-native, user-friendly)
export type QueryOperator =
	| "eq"
	| "ne"
	| "contains"
	| "starts_with"
	| "ends_with"
	| "gt"
	| "gte"
	| "lt"
	| "lte"
	| "in"
	| "is_null"
	| "has_key";

export interface QueryFilter {
	eq?: unknown;
	ne?: unknown;
	contains?: string;
	starts_with?: string;
	ends_with?: string;
	gt?: unknown;
	gte?: unknown;
	lt?: unknown;
	lte?: unknown;
	in?: unknown[];
	is_null?: boolean;
	has_key?: boolean;
}

// Default query values matching API defaults
const DEFAULT_QUERY: DocumentQuery = {
	order_dir: "asc",
	limit: 100,
	offset: 0,
	skip_count: false,
};

// =============================================================================
// Table Hooks
// =============================================================================

/**
 * Hook to fetch all tables (list endpoint — scope is valid here)
 *
 * `includeOrphaned` surfaces tables left behind by an uninstalled Solution
 * (rows carrying `orphaned_at`), which are hidden by default.
 */
export function useTables(scope?: string, includeOrphaned = false) {
	const query: { scope?: string; include_orphaned?: boolean } = {};
	if (scope) query.scope = scope;
	if (includeOrphaned) query.include_orphaned = true;
	return $api.useQuery(
		"get",
		"/api/tables",
		Object.keys(query).length > 0 ? { params: { query } } : undefined,
	);
}

/**
 * Hook to fetch a single table by UUID
 */
export function useTable(tableId: string) {
	return $api.useQuery(
		"get",
		"/api/tables/{table_id}",
		{
			params: {
				path: { table_id: tableId },
			},
		},
		{ enabled: !!tableId },
	);
}

/**
 * Hook to create a new table
 */
export function useCreateTable() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/tables", {
		onSuccess: (data) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/tables"] });
			toast.success("Table created", {
				description: `Table "${data.name}" has been created`,
			});
		},
	});
}

/**
 * Hook to update a table
 */
export function useUpdateTable() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/tables/{table_id}", {
		onSuccess: (data) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/tables"] });
			toast.success("Table updated", {
				description: `Table "${data.name}" has been updated`,
			});
		},
	});
}

/**
 * Hook to delete a table
 */
export function useDeleteTable() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/tables/{table_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/tables"] });
			toast.success("Table deleted");
		},
	});
}

// =============================================================================
// Document Hooks
// =============================================================================

/**
 * Hook to query documents with filtering and pagination
 */
export function useDocuments(
	tableId: string,
	query: Partial<DocumentQuery> = {},
) {
	const fullQuery: DocumentQuery = { ...DEFAULT_QUERY, ...query };

	return $api.useQuery(
		"post",
		"/api/tables/{table_id}/documents/query",
		{
			params: {
				path: { table_id: tableId },
			},
			body: fullQuery,
		},
		{ enabled: !!tableId },
	);
}

/**
 * Hook to count documents in a table
 */
export function useDocumentCount(tableId: string) {
	return $api.useQuery(
		"get",
		"/api/tables/{table_id}/documents/count",
		{
			params: {
				path: { table_id: tableId },
			},
		},
		{ enabled: !!tableId },
	);
}

/**
 * Hook to insert a new document
 */
export function useInsertDocument() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/tables/{table_id}/documents", {
		onSuccess: () => {
			// Invalidate document queries
			queryClient.invalidateQueries({
				queryKey: ["post", "/api/tables/{table_id}/documents/query"],
			});
			toast.success("Document created", {
				description: "Document has been inserted",
			});
		},
	});
}

/**
 * Hook to update a document
 */
export function useUpdateDocument() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"patch",
		"/api/tables/{table_id}/documents/{doc_id}",
		{
			onSuccess: () => {
				// Invalidate document queries
				queryClient.invalidateQueries({
					queryKey: [
						"post",
						"/api/tables/{table_id}/documents/query",
					],
				});
				toast.success("Document updated", {
					description: "Document has been updated",
				});
			},
		},
	);
}

/**
 * Hook to delete a document
 */
export function useDeleteDocument() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"delete",
		"/api/tables/{table_id}/documents/{doc_id}",
		{
			onSuccess: () => {
				// Invalidate document queries
				queryClient.invalidateQueries({
					queryKey: [
						"post",
						"/api/tables/{table_id}/documents/query",
					],
				});
				toast.success("Document deleted", {
					description: "Document has been deleted",
				});
			},
		},
	);
}

// =============================================================================
// Imperative API Functions (for use outside React components)
// =============================================================================

/**
 * List tables (imperative)
 */
export async function listTables(scope?: string): Promise<TableListResponse> {
	const { data, error } = await apiClient.GET("/api/tables", {
		params: {
			query: scope ? { scope } : undefined,
		},
	});
	if (error) throw new Error("Failed to list tables");
	return data;
}

/**
 * Get a table by UUID (imperative)
 */
export async function getTable(tableId: string): Promise<TablePublic> {
	const { data, error } = await apiClient.GET("/api/tables/{table_id}", {
		params: {
			path: { table_id: tableId },
		},
	});
	if (error) throw new Error("Failed to get table");
	return data;
}

/**
 * Create a table (imperative)
 */
export async function createTable(
	tableData: TableCreate,
	scope?: string,
): Promise<TablePublic> {
	const { data, error } = await apiClient.POST("/api/tables", {
		params: {
			query: scope ? { scope } : undefined,
		},
		body: tableData,
	});
	if (error) throw new Error("Failed to create table");
	return data;
}

/**
 * Update a table (imperative)
 */
export async function updateTable(
	tableId: string,
	tableData: TableUpdate,
): Promise<TablePublic> {
	const { data, error } = await apiClient.PATCH("/api/tables/{table_id}", {
		params: {
			path: { table_id: tableId },
		},
		body: tableData,
	});
	if (error) throw new Error("Failed to update table");
	return data;
}

/**
 * Delete a table (imperative)
 */
export async function deleteTable(tableId: string): Promise<void> {
	const { error } = await apiClient.DELETE("/api/tables/{table_id}", {
		params: {
			path: { table_id: tableId },
		},
	});
	if (error) throw new Error("Failed to delete table");
}

/**
 * Query documents (imperative)
 */
export async function queryDocuments(
	tableId: string,
	query: Partial<DocumentQuery> = {},
): Promise<DocumentListResponse> {
	const fullQuery: DocumentQuery = { ...DEFAULT_QUERY, ...query };
	const { data, error } = await apiClient.POST(
		"/api/tables/{table_id}/documents/query",
		{
			params: {
				path: { table_id: tableId },
			},
			body: fullQuery,
		},
	);
	if (error) throw new Error("Failed to query documents");
	return data;
}

/**
 * Insert a document (imperative)
 */
export async function insertDocument(
	tableId: string,
	documentData: DocumentCreate,
): Promise<DocumentPublic> {
	const { data, error } = await apiClient.POST(
		"/api/tables/{table_id}/documents",
		{
			params: {
				path: { table_id: tableId },
			},
			body: documentData,
		},
	);
	if (error) throw new Error("Failed to insert document");
	return data;
}

/**
 * Get a document by ID (imperative)
 */
export async function getDocument(
	tableId: string,
	documentId: string,
): Promise<DocumentPublic> {
	const { data, error } = await apiClient.GET(
		"/api/tables/{table_id}/documents/{doc_id}",
		{
			params: {
				path: { table_id: tableId, doc_id: documentId },
			},
		},
	);
	if (error) throw new Error("Failed to get document");
	return data;
}

/**
 * Update a document (imperative)
 */
export async function updateDocument(
	tableId: string,
	documentId: string,
	documentData: DocumentUpdate,
): Promise<DocumentPublic> {
	const { data, error } = await apiClient.PATCH(
		"/api/tables/{table_id}/documents/{doc_id}",
		{
			params: {
				path: { table_id: tableId, doc_id: documentId },
			},
			body: documentData,
		},
	);
	if (error) throw new Error("Failed to update document");
	return data;
}

/**
 * Delete a document (imperative)
 */
export async function deleteDocument(
	tableId: string,
	documentId: string,
): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/tables/{table_id}/documents/{doc_id}",
		{
			params: {
				path: { table_id: tableId, doc_id: documentId },
			},
		},
	);
	if (error) throw new Error("Failed to delete document");
}

/**
 * Validate a TablePolicies document without persisting it.
 *
 * The endpoint always returns 200 — validation outcome is in the body.
 * Errors come back as `{path, message}` entries; an `ok: true` response
 * has an empty `errors` array. Used by `PolicyEditor` to render
 * server-authoritative semantic errors inline below the parse-error row.
 *
 * The body parameter is `unknown` so the editor can hand the parsed AST
 * straight through without a redundant type narrowing — the server runs
 * the same validator either way and is the source of truth here.
 */
export async function validatePolicies(
	body: TablePolicies | unknown,
	options: { signal?: AbortSignal } = {},
): Promise<PolicyValidationResponse> {
	const { data, error } = await apiClient.POST(
		"/api/tables/policies/validate",
		{
			body: body as TablePolicies,
			signal: options.signal,
		},
	);
	if (error) {
		throw new Error(
			(error as { detail?: string }).detail ||
				"Failed to validate policies",
		);
	}
	return data;
}

/**
 * Count documents (imperative)
 */
export async function countDocuments(tableId: string): Promise<number> {
	const { data, error } = await apiClient.GET(
		"/api/tables/{table_id}/documents/count",
		{
			params: {
				path: { table_id: tableId },
			},
		},
	);
	if (error) throw new Error("Failed to count documents");
	return data.count;
}
