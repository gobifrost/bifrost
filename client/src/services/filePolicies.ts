import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type FilePolicyAction = "read" | "write" | "delete" | "list";

export interface FilePolicyRule {
	name: string;
	description?: string | null;
	actions: FilePolicyAction[];
	when?: unknown;
}

export type PolicyRuleRef = components["schemas"]["PolicyRuleRef"];

/** The portable policy document (the inner `{ policies: [...] }`). */
export interface FilePolicies {
	policies: (FilePolicyRule | PolicyRuleRef)[];
}

export interface FilePolicy {
	id?: string;
	location: string;
	path: string;
	organizationId?: string | null;
	policies: FilePolicies;
}

export interface FilePolicyListResponse {
	policies: FilePolicy[];
}

export interface FileAccessTestRequest {
	path: string;
	location: string;
	action: FilePolicyAction;
	scope?: string | null;
	userId?: string;
}

export interface FileAccessTestResult {
	allowed: boolean;
	path: string;
	location: string;
	action: FilePolicyAction;
	matchedPolicy?: string | null;
	matchedRule?: string | null;
	denialReason?: string | null;
}

async function parseResponse<T>(response: Response): Promise<T> {
	if (response.ok) {
		if (response.status === 204) return undefined as T;
		return (await response.json()) as T;
	}
	let detail = response.statusText;
	try {
		const body = (await response.json()) as { detail?: unknown; message?: unknown };
		const raw = body.detail ?? body.message;
		detail = typeof raw === "string" ? raw : JSON.stringify(raw ?? body);
	} catch {
		detail = await response.text().catch(() => response.statusText);
	}
	throw new Error(detail || `Request failed: ${response.status}`);
}

function withQuery(path: string, params: Record<string, string | null | undefined>) {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== null && value !== "") {
			query.set(key, value);
		}
	}
	const qs = query.toString();
	return qs ? `${path}?${qs}` : path;
}

export async function listFilePolicies(params: {
	location?: string;
	scope?: string | null;
	prefix?: string;
} = {}): Promise<FilePolicyListResponse> {
	const response = await authFetch(
		withQuery("/api/files/policies", {
			location: params.location,
			organization_id: params.scope ?? undefined,
			path: params.prefix,
		}),
	);
	const result = await parseResponse<{
		policies?: Array<
			FilePolicy | {
				id?: string;
				location: string;
				path: string;
				organization_id?: string | null;
				policies: FilePolicy["policies"];
			}
		>;
	}>(response);
	return {
		policies: (result.policies ?? []).map((policy) => {
			const raw = policy as FilePolicy & { organization_id?: string | null };
			return {
				id: raw.id,
				location: raw.location,
				path: raw.path,
				organizationId: raw.organizationId ?? raw.organization_id ?? null,
				policies: raw.policies,
			};
		}),
	};
}

export async function saveFilePolicy(policy: FilePolicy): Promise<FilePolicy> {
	const response = await authFetch(
		withQuery(`/api/files/policies/${encodeURIComponent(policy.path)}`, {
			location: policy.location,
			scope: policy.organizationId ?? undefined,
		}),
		{
			method: "PUT",
			body: JSON.stringify({
				policies: policy.policies,
			}),
		},
	);
	const result = await parseResponse<
		FilePolicy & { organization_id?: string | null }
	>(response);
	return {
		id: result.id,
		location: result.location,
		path: result.path,
		organizationId: result.organizationId ?? result.organization_id ?? null,
		policies: result.policies,
	};
}

export async function deleteFilePolicy(policy: FilePolicy): Promise<void> {
	const response = await authFetch(
		withQuery(`/api/files/policies/${encodeURIComponent(policy.path)}`, {
			location: policy.location,
			scope: policy.organizationId ?? undefined,
		}),
		{ method: "DELETE" },
	);
	await parseResponse<void>(response);
}

export async function testFileAccess(
	request: FileAccessTestRequest,
): Promise<FileAccessTestResult> {
	const response = await authFetch("/api/files/policies/test", {
		method: "POST",
		body: JSON.stringify({
			path: request.path,
			location: request.location,
			action: request.action,
			scope: request.scope ?? null,
			user_id: request.userId || null,
		}),
	});
	const result = await parseResponse<{
		allowed: boolean;
		path: string;
		location: string;
		action: FilePolicyAction;
		matched_policy?: string | null;
		matched_rule?: string | null;
		denial_reason?: string | null;
	}>(response);
	return {
		allowed: result.allowed,
		path: result.path,
		location: result.location,
		action: result.action,
		matchedPolicy: result.matched_policy ?? null,
		matchedRule: result.matched_rule ?? null,
		denialReason: result.denial_reason ?? null,
	};
}

const FILE_ACTIONS: FilePolicyAction[] = ["read", "write", "delete", "list"];

/**
 * The resolved policy cascade affecting a path: every policy in this
 * location/scope whose prefix matches `path`, longest-prefix first (so the
 * first entry is the winning override). No new endpoint — derived client-side
 * from `listFilePolicies`, mirroring the backend longest-prefix selection.
 */
export async function effectiveAccess(
	location: string,
	path: string,
	scope: string | null,
): Promise<FilePolicy[]> {
	const { policies } = await listFilePolicies({
		location,
		scope: scope ?? undefined,
	});
	return policies
		.filter((p) => p.location === location && path.startsWith(p.path))
		.sort((a, b) => b.path.length - a.path.length);
}

/**
 * Resolve all four file actions for one principal at one path, returning a
 * per-action result keyed by action.
 */
export async function testAllActions(req: {
	location: string;
	path: string;
	scope: string | null;
	userId: string;
}): Promise<Record<FilePolicyAction, FileAccessTestResult>> {
	const results = await Promise.all(
		FILE_ACTIONS.map((action) =>
			testFileAccess({
				location: req.location,
				path: req.path,
				scope: req.scope,
				userId: req.userId,
				action,
			}).then((r) => [action, r] as const),
		),
	);
	return Object.fromEntries(results) as Record<
		FilePolicyAction,
		FileAccessTestResult
	>;
}
