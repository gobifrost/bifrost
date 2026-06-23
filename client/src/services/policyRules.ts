import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PolicyRule = components["schemas"]["PolicyRulePublic"];
export type PolicyRuleUsages = components["schemas"]["PolicyRuleUsagesPublic"];

export async function listPolicyRules(domain?: "file" | "table"): Promise<PolicyRule[]> {
	const { data, error } = await apiClient.GET("/api/policy-rules", {
		params: {
			query: domain ? { domain } : undefined,
		},
	});
	if (error) throw new Error(typeof error === "object" && "detail" in error ? String((error as { detail: unknown }).detail) : "Failed to list policy rules");
	return data ?? [];
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
