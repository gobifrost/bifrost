import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type KubernetesStatus = components["schemas"]["KubernetesStatus"];
export type KubernetesExecutionSettings =
	components["schemas"]["KubernetesExecutionSettings"];
export type KubernetesExecutionJobType =
	components["schemas"]["KubernetesExecutionJobType"];
export type KubernetesPolicyValues =
	components["schemas"]["KubernetesPolicyValues"];
export type KubernetesPolicyUpdate =
	components["schemas"]["KubernetesPolicyUpdate"];

/** A refused request; `field` names the setting a validation error is about. */
export class KubernetesRequestError extends Error {
	constructor(
		message: string,
		readonly status: number,
		readonly field?: string,
	) {
		super(message);
		this.name = "KubernetesRequestError";
	}
}

type ValidationDetail = { loc?: unknown; msg?: unknown };

function requestError(status: number, statusText: string, body: unknown) {
	const detail = (body as { detail?: unknown } | null)?.detail;
	if (typeof detail === "string") {
		return new KubernetesRequestError(detail, status);
	}
	if (Array.isArray(detail) && detail.length > 0) {
		const first = detail[0] as ValidationDetail;
		const loc = Array.isArray(first.loc) ? first.loc : [];
		const field = loc.length > 1 ? String(loc[loc.length - 1]) : undefined;
		if (typeof first.msg === "string") {
			return new KubernetesRequestError(first.msg, status, field);
		}
	}
	return new KubernetesRequestError(
		`Kubernetes request failed: ${statusText}`,
		status,
	);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw requestError(response.status, response.statusText, body);
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

/** Change some scaling and isolation settings; omitted ones stay. */
export function updateKubernetesPolicy(
	changes: KubernetesPolicyUpdate,
): Promise<KubernetesExecutionSettings> {
	return request("/api/admin/kubernetes/policy", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(changes),
	});
}
