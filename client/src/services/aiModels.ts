import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type AIProviderKind =
	components["schemas"]["AIProviderConnectionResponse"]["provider"];
export type AIModelAssignmentKey =
	components["schemas"]["AIModelAssignmentResponse"]["assignment_key"];
export type ModelCapabilities = components["schemas"]["ModelCapabilities"];
export type AIProviderConnection =
	components["schemas"]["AIProviderConnectionResponse"];
export type AIProviderConnectionSummary =
	components["schemas"]["AIProviderConnectionSummary"];
export type AIModelProfile = components["schemas"]["AIModelProfileResponse"];
export type AIModelAssignment =
	components["schemas"]["AIModelAssignmentResponse"];
export type AIProviderConnectionCreate =
	components["schemas"]["AIProviderConnectionCreate"];
export type AIProviderConnectionUpdate =
	components["schemas"]["AIProviderConnectionUpdate"];
export type AIModelProfileCreate =
	components["schemas"]["AIModelProfileCreate"];
export type AIModelProfileUpdate =
	components["schemas"]["AIModelProfileUpdate"];
export type AIModelProfileMergeRequest =
	components["schemas"]["AIModelProfileMergeRequest"];
export type AIModelProfileMergeResponse =
	components["schemas"]["AIModelProfileMergeResponse"];
export type AIConnectionTestResponse =
	components["schemas"]["AIConnectionTestResponse"];
export type AIModelsResponse = components["schemas"]["AIModelsResponse"];

function throwApiError(action: string, error: unknown): never {
	const detail =
		error && typeof error === "object" && "detail" in error
			? String(error.detail)
			: JSON.stringify(error);
	throw new Error(`Failed to ${action}: ${detail}`);
}

export async function listProviderConnections(): Promise<
	AIProviderConnection[]
> {
	const { data, error } = await apiClient.GET("/api/admin/ai/connections");
	if (error) throwApiError("list provider connections", error);
	return data as AIProviderConnection[];
}

export async function createProviderConnection(
	data: AIProviderConnectionCreate,
): Promise<AIProviderConnection> {
	const { data: responseData, error } = await apiClient.POST(
		"/api/admin/ai/connections",
		{ body: data },
	);
	if (error) throwApiError("create provider connection", error);
	return responseData as AIProviderConnection;
}

export async function verifyProviderConnection(
	data: AIProviderConnectionCreate,
): Promise<AIConnectionTestResponse> {
	const { data: responseData, error } = await apiClient.POST(
		"/api/admin/ai/connections/verify",
		{ body: data },
	);
	if (error) throwApiError("verify provider connection", error);
	return responseData as AIConnectionTestResponse;
}

export async function updateProviderConnection(
	id: string,
	data: AIProviderConnectionUpdate,
): Promise<AIProviderConnection> {
	const { data: responseData, error } = await apiClient.PATCH(
		"/api/admin/ai/connections/{connection_id}",
		{
			params: { path: { connection_id: id } },
			body: data,
		},
	);
	if (error) throwApiError("update provider connection", error);
	return responseData as AIProviderConnection;
}

export async function deleteProviderConnection(id: string): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/admin/ai/connections/{connection_id}",
		{
			params: { path: { connection_id: id } },
		},
	);
	if (error) throwApiError("delete provider connection", error);
}

export async function testProviderConnection(
	id: string,
): Promise<AIConnectionTestResponse> {
	const { data, error } = await apiClient.POST(
		"/api/admin/ai/connections/{connection_id}/test",
		{
			params: { path: { connection_id: id } },
		},
	);
	if (error) throwApiError("test provider connection", error);
	return data as AIConnectionTestResponse;
}

export async function listProviderModels(
	id: string,
): Promise<AIModelsResponse> {
	const { data, error } = await apiClient.GET(
		"/api/admin/ai/connections/{connection_id}/models",
		{
			params: { path: { connection_id: id } },
		},
	);
	if (error) throwApiError("list provider models", error);
	return data as AIModelsResponse;
}

export async function listModelProfiles(): Promise<AIModelProfile[]> {
	const { data, error } = await apiClient.GET("/api/admin/ai/profiles");
	if (error) throwApiError("list model profiles", error);
	return data as AIModelProfile[];
}

export async function createModelProfile(
	data: AIModelProfileCreate,
): Promise<AIModelProfile> {
	const { data: responseData, error } = await apiClient.POST(
		"/api/admin/ai/profiles",
		{ body: data },
	);
	if (error) throwApiError("create model profile", error);
	return responseData as AIModelProfile;
}

export async function updateModelProfile(
	id: string,
	data: AIModelProfileUpdate,
): Promise<AIModelProfile> {
	const { data: responseData, error } = await apiClient.PATCH(
		"/api/admin/ai/profiles/{profile_id}",
		{
			params: { path: { profile_id: id } },
			body: data,
		},
	);
	if (error) throwApiError("update model profile", error);
	return responseData as AIModelProfile;
}

export async function deleteModelProfile(id: string): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/admin/ai/profiles/{profile_id}",
		{
			params: { path: { profile_id: id } },
		},
	);
	if (error) throwApiError("delete model profile", error);
}

export async function mergeModelProfiles(
	data: AIModelProfileMergeRequest,
): Promise<AIModelProfileMergeResponse> {
	const { data: responseData, error } = await apiClient.POST(
		"/api/admin/ai/profiles/merge",
		{ body: data },
	);
	if (error) throwApiError("merge model profiles", error);
	return responseData as AIModelProfileMergeResponse;
}

export async function listModelAssignments(): Promise<AIModelAssignment[]> {
	const { data, error } = await apiClient.GET("/api/admin/ai/assignments");
	if (error) throwApiError("list model assignments", error);
	return data as AIModelAssignment[];
}

export async function setModelAssignment(
	assignmentKey: AIModelAssignmentKey,
	profileId: string,
): Promise<AIModelAssignment> {
	const { data, error } = await apiClient.PUT(
		"/api/admin/ai/assignments/{assignment_key}",
		{
			params: { path: { assignment_key: assignmentKey } },
			body: { profile_id: profileId },
		},
	);
	if (error) throwApiError("set model assignment", error);
	return data as AIModelAssignment;
}

export async function clearModelAssignment(
	assignmentKey: AIModelAssignmentKey,
): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/admin/ai/assignments/{assignment_key}",
		{
			params: { path: { assignment_key: assignmentKey } },
		},
	);
	if (error) throwApiError("clear model assignment", error);
}
