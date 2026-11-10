import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type GitAppSetup = components["schemas"]["GitAppSetupResponse"];
export type GitAppRepository = components["schemas"]["GitAppRepository"];
type ManifestRequest = components["schemas"]["GitAppManifestRequest"];
type ManifestResponse = components["schemas"]["GitAppManifestResponse"];
type ExistingRequest = components["schemas"]["GitAppExistingRequest"];
type AuthorizeResponse = components["schemas"]["GitAppAuthorizeResponse"];

async function request<T>(path: string, body?: unknown): Promise<T> {
	const response = await authFetch(
		path,
		body === undefined
			? undefined
			: {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(body),
				},
	);
	const data = await response.json();
	if (!response.ok)
		throw new Error(
			typeof data.detail === "string"
				? data.detail
				: "GitHub setup could not be completed.",
		);
	return data as T;
}

export const getGitAppSetup = () =>
	request<GitAppSetup | null>("/api/github/app/setup");
export const createGitApp = (body: ManifestRequest) =>
	request<ManifestResponse>("/api/github/app/manifest", body);
export const configureExistingGitApp = (body: ExistingRequest) =>
	request<GitAppSetup>("/api/github/app/existing", body);
export const authorizeGitApp = (setup_id: string) =>
	request<AuthorizeResponse>("/api/github/app/authorize", { setup_id });
export const refreshGitAppInstallations = (setup_id: string) =>
	request<GitAppSetup>("/api/github/app/installations", { setup_id });
export const listGitAppRepositories = (
	setup_id: string,
	installation_id: number,
) =>
	request<GitAppSetup>("/api/github/app/repositories", {
		setup_id,
		installation_id,
	});
export const selectGitAppRepository = (
	setup_id: string,
	installation_id: number,
	repository_id: number,
) =>
	request<GitAppSetup>("/api/github/app/select", {
		setup_id,
		installation_id,
		repository_id,
	});

export function takeGitAppCallback(): { code: string; state: string } | null {
	const url = new URL(window.location.href);
	const code = url.searchParams.get("code");
	const state = url.searchParams.get("state");
	const error = url.searchParams.get("error");
	for (const key of [
		"code",
		"state",
		"error",
		"error_description",
		"installation_id",
		"setup_action",
	])
		url.searchParams.delete(key);
	window.history.replaceState(
		window.history.state,
		"",
		url.pathname + url.search + url.hash,
	);
	if (error)
		throw new Error(
			"GitHub authorization was cancelled. Your current connection is unchanged.",
		);
	return code && state ? { code, state } : null;
}

export function completeGitAppCallback(callback: {
	code: string;
	state: string;
}) {
	const path = callback.state.startsWith("manifest.")
		? "/api/github/app/manifest/complete"
		: "/api/github/app/authorize/complete";
	return request<GitAppSetup>(path, callback);
}

export function submitGitAppManifest(result: ManifestResponse) {
	const target = new URL(result.registration_url);
	if (
		target.origin !== "https://github.com" ||
		!/^\/(?:settings\/apps\/new|organizations\/[A-Za-z0-9-]+\/settings\/apps\/new)$/.test(
			target.pathname,
		)
	)
		throw new Error("Invalid GitHub registration destination.");
	const form = document.createElement("form");
	form.method = "POST";
	form.action = target.href;
	const input = document.createElement("input");
	input.type = "hidden";
	input.name = "manifest";
	input.value = JSON.stringify(result.manifest);
	form.append(input);
	document.body.append(form);
	form.submit();
	form.remove();
}

export function openGitAppAuthorization(url: string) {
	const target = new URL(url);
	if (
		target.origin !== "https://github.com" ||
		target.pathname !== "/login/oauth/authorize"
	)
		throw new Error("Invalid GitHub authorization destination.");
	window.location.assign(target.href);
}
