import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type KubernetesStatus =
	components["schemas"]["KubernetesStatus"];
export type KubernetesExecutionSettings =
	components["schemas"]["KubernetesExecutionSettings"];
export type KubernetesExecutionJobType =
	components["schemas"]["KubernetesExecutionJobType"];

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(
			typeof body.detail === "string"
				? body.detail
				: `Kubernetes request failed: ${response.statusText}`,
		);
	}
	return response.json() as Promise<T>;
}

export function getKubernetesStatus(): Promise<KubernetesStatus> {
	return request("/api/admin/kubernetes/status");
}

export function getKubernetesExecution(): Promise<KubernetesExecutionSettings> {
	return request("/api/admin/kubernetes/execution");
}

export function updateKubernetesExecution(
	jobType: string,
	update: { enabled: boolean; maxConcurrency?: number | null },
): Promise<KubernetesExecutionSettings> {
	return request("/api/admin/kubernetes/execution", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			job_type: jobType,
			enabled: update.enabled,
			...(update.maxConcurrency !== undefined
				? { max_concurrency: update.maxConcurrency }
				: {}),
		}),
	});
}
