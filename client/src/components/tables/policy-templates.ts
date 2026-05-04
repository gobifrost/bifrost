/**
 * Built-in policy templates surfaced in the PolicyEditor's "Insert template..."
 * menu. These mirror the canonical examples in
 * docs/superpowers/specs/2026-04-30-table-policies-design.md.
 *
 * Each template is a complete `Policy` shape (name, description, actions, when)
 * — the editor inserts a deep copy so subsequent edits don't mutate the source.
 */

import type { components } from "@/lib/v1";

export type Policy = components["schemas"]["Policy"];

export const POLICY_TEMPLATES: Record<string, Policy> = {
	admin_bypass: {
		name: "admin_bypass",
		description: "Platform admins can do anything",
		actions: ["read", "create", "update", "delete"],
		when: { user: "is_platform_admin" },
	},
	own_row: {
		name: "own_row",
		description: "Row owner can read/update/delete",
		actions: ["read", "update", "delete"],
		when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
	},
	own_org: {
		name: "own_org",
		description:
			"Caller can see rows in their own org (requires organization_id field on row)",
		actions: ["read"],
		when: { eq: [{ row: "organization_id" }, { user: "organization_id" }] },
	},
	role_gated_read: {
		name: "role_gated_read",
		description: "Specific role can read",
		actions: ["read"],
		when: { call: "has_role", args: ["YOUR_ROLE_NAME"] },
	},
};

export type PolicyTemplateKey = keyof typeof POLICY_TEMPLATES;

/**
 * Return a deep copy of a template so the caller can mutate freely without
 * polluting the constant.
 */
export function instantiateTemplate(key: PolicyTemplateKey): Policy {
	return JSON.parse(JSON.stringify(POLICY_TEMPLATES[key])) as Policy;
}
