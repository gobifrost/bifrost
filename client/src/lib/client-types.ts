/**
 * Client-only types that extend or complement the API types
 * These are NOT generated from the OpenAPI spec
 */

import type { components } from "./v1";

// ==================== EXECUTION TYPES ====================

export interface ExecutionLog {
	timestamp: string;
	level: "info" | "warning" | "error";
	message: string;
	metadata?: Record<string, unknown>;
}

export interface ExecutionFilters {
	status?: components["schemas"]["ExecutionStatus"];
	workflow_name?: string;
	workflow_id?: string;
	start_date?: string;
	end_date?: string;
	limit?: number;
	excludeLocal?: boolean;
}

export interface ExecutionListResponse {
	executions: components["schemas"]["WorkflowExecution"][];
	total: number;
	page: number;
	pageSize: number;
}

// ==================== FORM TYPES ====================

/**
 * Form field types - mirrors backend FormFieldType enum
 */
export type FormFieldType =
	| "text"
	| "email"
	| "number"
	| "select"
	| "multi_select"
	| "checkbox"
	| "textarea"
	| "radio"
	| "date"
	| "datetime"
	| "markdown"
	| "html"
	| "file";

/**
 * Data provider input configuration modes
 */
export type DataProviderInputMode = "static" | "fieldRef" | "expression";

/**
 * Configuration for a single data provider input parameter
 */
export interface DataProviderInputConfig {
	mode: DataProviderInputMode;
	value?: string | null;
	field_name?: string | null;
	expression?: string | null;
}

/**
 * Form field validation rules
 */
export interface FormFieldValidation {
	pattern?: string | null;
	min?: number | null;
	max?: number | null;
	message?: string | null;
}

/**
 * Form field definition (input for creating/editing)
 */
export interface FormField {
	name: string;
	label?: string | null;
	type: FormFieldType;
	required?: boolean;
	validation?: FormFieldValidation | null;
	data_provider_id?: string | null;
	has_dynamic_options?: boolean;
	data_provider_inputs?: Record<string, DataProviderInputConfig> | null;
	default_value?: unknown;
	placeholder?: string | null;
	help_text?: string | null;
	visibility_expression?: string | null;
	options?: Array<{ label: string; value: string }> | null;
	allowed_types?: string[] | null;
	multiple?: boolean | null;
	max_size_mb?: number | null;
	content?: string | null;
	allow_as_query_param?: boolean | null;
	auto_fill?: Record<string, string> | null;
}

/**
 * Form schema with field definitions
 */
export interface FormSchema {
	fields: FormField[];
}

export interface FormSubmission {
	form_id: string;
	form_data: Record<string, unknown>;
}

export interface FormExecutionResponse {
	execution_id: string;
	status: components["schemas"]["ExecutionStatus"];
	result?: unknown;
	error_message?: string;
}

// ==================== OAUTH TYPES ====================

export interface OAuthAuthorizeResponse {
	authorization_url: string;
	state: string;
	message: string;
}

export interface OAuthProviderPreset {
	name: string;
	displayName: string;
	oauth_flow_type: "authorization_code" | "client_credentials";
	authorization_url: string;
	token_url: string;
	default_scopes: string;
	documentation_url: string;
	icon?: string;
}

export const OAUTH_PROVIDER_PRESETS: Record<string, OAuthProviderPreset> = {
	microsoft_graph: {
		name: "microsoft_graph",
		displayName: "Microsoft Graph",
		oauth_flow_type: "authorization_code",
		authorization_url:
			"https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
		token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
		default_scopes: "User.Read Mail.Read",
		documentation_url: "https://learn.microsoft.com/en-us/graph/auth/",
		icon: "🔷",
	},
	google: {
		name: "google",
		displayName: "Google APIs",
		oauth_flow_type: "authorization_code",
		authorization_url: "https://accounts.google.com/o/oauth2/v2/auth",
		token_url: "https://oauth2.googleapis.com/token",
		default_scopes: "https://www.googleapis.com/auth/userinfo.email",
		documentation_url:
			"https://developers.google.com/identity/protocols/oauth2",
		icon: "🔴",
	},
	github: {
		name: "github",
		displayName: "GitHub",
		oauth_flow_type: "authorization_code",
		authorization_url: "https://github.com/login/oauth/authorize",
		token_url: "https://github.com/login/oauth/access_token",
		default_scopes: "repo user",
		documentation_url:
			"https://docs.github.com/en/developers/apps/building-oauth-apps/authorizing-oauth-apps",
		icon: "⚫",
	},
	azure_ad: {
		name: "azure_ad",
		displayName: "Azure AD",
		oauth_flow_type: "client_credentials",
		authorization_url:
			"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
		token_url:
			"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
		default_scopes: "https://graph.microsoft.com/.default",
		documentation_url:
			"https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
		icon: "🔷",
	},
};

// OAuth helper functions
export function getStatusColor(status: string): string {
	switch (status) {
		case "completed":
			return "green";
		case "not_connected":
			return "gray";
		case "waiting_callback":
		case "testing":
			return "yellow";
		case "failed":
			return "red";
		default:
			return "gray";
	}
}

export function getStatusLabel(status: string): string {
	switch (status) {
		case "completed":
			return "Connected";
		case "not_connected":
			return "Not Connected";
		case "waiting_callback":
			return "Waiting for Authorization";
		case "testing":
			return "Testing Connection";
		case "failed":
			return "Failed";
		default:
			return status;
	}
}

export function isExpired(expires_at?: string): boolean {
	if (!expires_at) return true;
	return new Date(expires_at) <= new Date();
}

export function expiresSoon(
	expires_at?: string,
	minutesThreshold: number = 45,
): boolean {
	if (!expires_at) return true;
	const expiresDate = new Date(expires_at);
	const thresholdDate = new Date(Date.now() + minutesThreshold * 60 * 1000);
	return expiresDate <= thresholdDate;
}

// ==================== CHAT TYPES ====================
// Re-export generated types from v1.d.ts for convenience
// Only client-only types (not from API) should be defined here

// Re-export generated API types
export type MessageRole = components["schemas"]["MessageRole"];
export type ToolCall = components["schemas"]["ToolCall"];
export type AgentSummary = components["schemas"]["AgentSummary"];
export type AgentPublic = components["schemas"]["AgentPublic"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationPublic = components["schemas"]["ConversationPublic"];
export type MessagePublic = components["schemas"]["MessagePublic"];
export type ConversationCreate = components["schemas"]["ConversationCreate"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type ChatResponse = components["schemas"]["ChatResponse"];

// Client-only types (not from API)
export interface ToolResult {
	tool_call_id: string;
	tool_name: string;
	result: unknown;
	error?: string | null;
	duration_ms?: number | null;
}

export interface ChatStreamChunk {
	type: "delta" | "tool_call" | "tool_result" | "done" | "error";
	content?: string | null;
	tool_call?: ToolCall | null;
	tool_result?: ToolResult | null;
	message_id?: string | null;
	token_count_input?: number | null;
	token_count_output?: number | null;
	duration_ms?: number | null;
	error?: string | null;
}

// ==================== TYPE ALIASES ====================

export type ConfigScope = "GLOBAL" | "org";
