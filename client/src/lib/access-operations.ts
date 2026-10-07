/**
 * Common operations to test access with, named the way people ask ("Read
 * Tables") and keyed the way the access list names them: a catalog id or
 * "METHOD /api/path". Any other access-list key can still be typed.
 */
export interface OperationSuggestion {
	label: string;
	operation: string;
}

export const OPERATION_SUGGESTIONS: readonly OperationSuggestion[] = [
	{ label: "Read Agents", operation: "agents.list" },
	{ label: "Read Apps", operation: "apps.list" },
	{ label: "Read and Write Apps", operation: "apps.update" },
	{ label: "Publish Apps", operation: "apps.publish" },
	{ label: "Read Configuration", operation: "configs.list" },
	{ label: "Read and Write Configuration", operation: "configs.update" },
	{ label: "Read Secret Values", operation: "POST /api/sdk/config/get" },
	{ label: "Read Forms", operation: "forms.list" },
	{ label: "Read and Write Forms", operation: "forms.update" },
	{ label: "Read Integrations", operation: "integrations.list" },
	{ label: "Read and Write Integrations", operation: "integrations.update" },
	{ label: "Read Knowledge", operation: "knowledge.namespaces.list" },
	{
		label: "Read and Write Knowledge",
		operation: "knowledge.documents.create",
	},
	{ label: "Read Organizations", operation: "organizations.list" },
	{ label: "Read Tables", operation: "tables.list" },
	{ label: "Read and Write Tables", operation: "tables.update" },
	{
		label: "Query Table Rows",
		operation: "POST /api/tables/{table_id}/documents/query",
	},
	{
		label: "Write Table Rows",
		operation: "POST /api/tables/{table_id}/documents",
	},
	{ label: "Read Users", operation: "users.list" },
	{ label: "Read and Write Users", operation: "users.create" },
	{ label: "Read Role Assignments", operation: "users.roles.list" },
	{
		label: "Read and Write Role Assignments",
		operation: "PUT /api/users/{user_id}/role-assignments",
	},
	{ label: "Read Workflows", operation: "workflows.list" },
	{ label: "Read and Write Workflows", operation: "workflows.update" },
	{ label: "Read Workflow Runs", operation: "executions.list" },
];

/**
 * The operation to test for what's in the Operation field: a suggestion's
 * name ("Read Tables", any case) is its operation; anything else is taken as
 * typed, an access-list key.
 */
export function operationFor(text: string): string {
	const typed = text.trim();
	const suggestion = OPERATION_SUGGESTIONS.find(
		({ label }) => label.toLowerCase() === typed.toLowerCase(),
	);
	return suggestion ? suggestion.operation : typed;
}
