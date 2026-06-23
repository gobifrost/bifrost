import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PolicyRule = components["schemas"]["PolicyRulePublic"];
export type PolicyRuleCreate = components["schemas"]["PolicyRuleCreate"];
export type PolicyRuleUpdate = components["schemas"]["PolicyRuleUpdate"];
export type PolicyRuleUsages = components["schemas"]["PolicyRuleUsagesPublic"];

/** Structured payload returned when a DELETE is rejected with 409 (rule in use). */
export interface PolicyRuleInUseError {
	type: "in_use";
	message: string;
	usages: PolicyRuleUsages;
}

function extractDetail(error: unknown): string {
	if (typeof error === "object" && error !== null && "detail" in error) {
		return String((error as { detail: unknown }).detail);
	}
	return "Unknown error";
}

export async function listPolicyRules(domain?: "file" | "table"): Promise<PolicyRule[]> {
	const { data, error } = await apiClient.GET("/api/policy-rules", {
		params: {
			query: domain ? { domain } : undefined,
		},
	});
	if (error) throw new Error(typeof error === "object" && "detail" in error ? String((error as { detail: unknown }).detail) : "Failed to list policy rules");
	return data ?? [];
}

export async function createPolicyRule(body: PolicyRuleCreate): Promise<PolicyRule> {
	const { data, error } = await apiClient.POST("/api/policy-rules", { body });
	if (error) throw new Error(extractDetail(error) || "Failed to create policy rule");
	if (data === undefined) throw new Error("No data returned from create policy rule");
	return data;
}

export async function updatePolicyRule(
	domain: string,
	name: string,
	body: PolicyRuleUpdate,
): Promise<PolicyRule> {
	const { data, error } = await apiClient.PUT(
		"/api/policy-rules/{domain}/{name}",
		{ params: { path: { domain, name } }, body },
	);
	if (error) throw new Error(extractDetail(error) || "Failed to update policy rule");
	if (data === undefined) throw new Error("No data returned from update policy rule");
	return data;
}

/**
 * Delete a policy rule. If the server returns 409 with a usages payload, this
 * function throws a plain `Error` whose `cause` is a `PolicyRuleInUseError` so
 * callers can distinguish "in use" (show blast radius) from other failures.
 */
export async function deletePolicyRule(domain: string, name: string): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/policy-rules/{domain}/{name}",
		{ params: { path: { domain, name } } },
	);
	if (!error) return;

	// Try to surface a structured in-use payload when the server sends 409.
	if (typeof error === "object" && error !== null && "detail" in error) {
		const detail = (error as { detail: unknown }).detail;
		if (typeof detail === "object" && detail !== null && "usages" in detail) {
			const inUse: PolicyRuleInUseError = {
				type: "in_use",
				message: "message" in detail ? String((detail as { message: unknown }).message) : `Policy rule '${name}' is in use`,
				usages: (detail as { usages: PolicyRuleUsages }).usages,
			};
			const err = new Error(inUse.message);
			(err as Error & { cause: PolicyRuleInUseError }).cause = inUse;
			throw err;
		}
		throw new Error(String(detail) || "Failed to delete policy rule");
	}
	throw new Error("Failed to delete policy rule");
}

export async function policyRuleUsages(domain: string, name: string): Promise<PolicyRuleUsages> {
	const { data, error } = await apiClient.GET("/api/policy-rules/{domain}/{name}/usages", {
		params: {
			path: { domain, name },
		},
	});
	if (error) throw new Error(typeof error === "object" && "detail" in error ? String((error as { detail: unknown }).detail) : "Failed to get policy rule usages");
	if (data === undefined) throw new Error("No data returned for policy rule usages");
	return data;
}
