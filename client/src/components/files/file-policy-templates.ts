/**
 * Built-in file-policy templates for the FilePolicyEditor's
 * "Insert template…" menu. File policies share the table policy AST but use
 * the file action vocabulary (read/write/delete/list) and an extra `{file: …}`
 * reference namespace (location, path, created_by, created_at).
 *
 * Each template is a complete rule shape; the editor inserts a deep copy.
 */

import type { FilePolicyRule } from "@/services/filePolicies";

export const FILE_POLICY_TEMPLATES: Record<string, FilePolicyRule> = {
	admin_bypass: {
		name: "admin_bypass",
		description: "Platform admins can do anything.",
		actions: ["read", "write", "delete", "list"],
		when: { user: "is_platform_admin" },
	},
	everyone_read: {
		name: "everyone_read",
		description: "Any authenticated user can read and list.",
		actions: ["read", "list"],
		when: null,
	},
	authed_write: {
		name: "authed_write",
		description: "Any authenticated user can write and delete.",
		actions: ["write", "delete"],
		when: null,
	},
	own_files: {
		name: "own_files",
		description: "The uploader can read/write/delete their own files.",
		actions: ["read", "write", "delete"],
		when: { eq: [{ file: "created_by" }, { user: "user_id" }] },
	},
	role_gated_read: {
		name: "role_gated_read",
		description: "Members of a specific role can read.",
		actions: ["read", "list"],
		when: { call: "has_role", args: ["YOUR_ROLE_NAME"] },
	},
};

export type FilePolicyTemplateKey = keyof typeof FILE_POLICY_TEMPLATES;

/** Deep copy of a template so edits don't mutate the constant. */
export function instantiateFileTemplate(
	key: FilePolicyTemplateKey,
): FilePolicyRule {
	return JSON.parse(
		JSON.stringify(FILE_POLICY_TEMPLATES[key]),
	) as FilePolicyRule;
}
