import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type BuilderRunnerSetup =
	components["schemas"]["SandboxRunnerSetupState"];
export type BuilderRunnerConfig =
	components["schemas"]["SandboxRunnerConfigPublic"];
export type BuilderRunnerConfigSave =
	components["schemas"]["SandboxRunnerConfigSave"];
export type BuilderRunnerProvisionJob =
	components["schemas"]["PlatformJobAccepted"];

const ENDPOINT = "/api/admin/builder/runner";

async function runnerError(response: Response, fallback: string): Promise<Error> {
	const body = await response.json().catch(() => null);
	const detail =
		body && typeof body === "object" && typeof body.detail === "string"
			? body.detail
			: fallback;
	return new Error(detail);
}

async function requestJson<T>(
	url: string,
	fallback: string,
	init: RequestInit = {},
): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) throw await runnerError(response, fallback);
	return (await response.json()) as T;
}

export async function getBuilderRunnerSetup(
	signal?: AbortSignal,
): Promise<BuilderRunnerSetup> {
	return requestJson<BuilderRunnerSetup>(ENDPOINT, "Failed to load Builder setup", {
		signal,
	});
}

export async function saveBuilderRunnerSetup(
	config: BuilderRunnerConfigSave,
): Promise<BuilderRunnerConfig> {
	return requestJson<BuilderRunnerConfig>(
		ENDPOINT,
		"Failed to save Builder runner settings",
		{ method: "PUT", body: JSON.stringify(config) },
	);
}

export async function provisionBuilderRunner(): Promise<BuilderRunnerProvisionJob> {
	return requestJson<BuilderRunnerProvisionJob>(
		`${ENDPOINT}/provision`,
		"Failed to start Builder runner setup",
		{ method: "POST" },
	);
}

export async function deleteBuilderRunnerSetup(): Promise<void> {
	const response = await authFetch(ENDPOINT, { method: "DELETE" });
	if (!response.ok) {
		throw await runnerError(response, "Failed to remove Builder runner settings");
	}
}
