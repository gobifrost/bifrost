import { beforeEach, describe, expect, it, vi } from "vitest";
import { authFetch } from "@/lib/api-client";
import {
	getGitAppSetup,
	createGitApp,
	authorizeGitApp,
	configureExistingGitApp,
	refreshGitAppInstallations,
	listGitAppRepositories,
	selectGitAppRepository,
	completeGitAppCallback,
	takeGitAppCallback,
	submitGitAppManifest,
	openGitAppAuthorization,
} from "./githubGitApp";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));

beforeEach(() => {
	vi.resetAllMocks();
	window.history.replaceState({}, "", "/settings/github");
});

describe("GitHub App setup service", () => {
	it("uses authenticated requests for each setup transition", async () => {
		vi.mocked(authFetch).mockImplementation(
			async () =>
				new Response(JSON.stringify({ stage: "authorization" })),
		);
		await getGitAppSetup();
		await createGitApp({
			owner: "org",
			name: "App",
			owner_type: "organization",
			public: false,
			workflows: false,
		});
		await configureExistingGitApp({
			app_id: 1,
			client_id: "client",
			private_key: "key",
			client_secret: "secret",
		});
		await authorizeGitApp("setup");
		await refreshGitAppInstallations("setup");
		await listGitAppRepositories("setup", 10);
		await selectGitAppRepository("setup", 10, 20);
		await completeGitAppCallback({ state: "manifest.state", code: "code" });
		await completeGitAppCallback({ state: "oauth.state", code: "code" });
		expect(vi.mocked(authFetch).mock.calls.map((call) => call[0])).toEqual([
			"/api/github/app/setup",
			"/api/github/app/manifest",
			"/api/github/app/existing",
			"/api/github/app/authorize",
			"/api/github/app/installations",
			"/api/github/app/repositories",
			"/api/github/app/select",
			"/api/github/app/manifest/complete",
			"/api/github/app/authorize/complete",
		]);
		expect(
			JSON.parse(vi.mocked(authFetch).mock.calls[5][1]?.body as string),
		).toEqual({ setup_id: "setup", installation_id: 10 });
	});

	it("clears callback secrets before passing them to the API", () => {
		window.history.replaceState(
			{},
			"",
			"/settings/github?code=one-use&state=manifest.state&installation_id=9",
		);
		expect(takeGitAppCallback()).toEqual({
			code: "one-use",
			state: "manifest.state",
		});
		expect(window.location.search).toBe("");
		expect(takeGitAppCallback()).toBeNull();
	});

	it("reports denial after removing the callback query", () => {
		window.history.replaceState(
			{},
			"",
			"/settings/github?error=access_denied&state=oauth.state",
		);
		expect(() => takeGitAppCallback()).toThrow("cancelled");
		expect(window.location.search).toBe("");
	});

	it("sends the manifest as a form POST without credentials", () => {
		const submit = vi
			.spyOn(HTMLFormElement.prototype, "submit")
			.mockImplementation(function (this: HTMLFormElement) {
				expect(this.method.toLowerCase()).toBe("post");
				expect(this.action).toBe(
					"https://github.com/settings/apps/new?state=state",
				);
				expect(new FormData(this).get("manifest")).toBe(
					'{"name":"App"}',
				);
			});
		submitGitAppManifest({
			setup_id: "id",
			stage: "registration",
			expires_at: "later",
			installations: [],
			repositories: [],
			registration_url:
				"https://github.com/settings/apps/new?state=state",
			manifest: { name: "App" },
		});
		expect(submit).toHaveBeenCalledOnce();
		expect(document.querySelector("form")).toBeNull();
	});

	it("rejects external registration and authorization destinations", () => {
		expect(() =>
			submitGitAppManifest({
				setup_id: "id",
				stage: "registration",
				expires_at: "later",
				installations: [],
				repositories: [],
				registration_url: "https://evil.example/settings/apps/new",
				manifest: {},
			}),
		).toThrow("Invalid");
		expect(() =>
			openGitAppAuthorization(
				"https://evil.example/login/oauth/authorize",
			),
		).toThrow("Invalid");
	});

	it("reports actionable provider errors", async () => {
		vi.mocked(authFetch).mockResolvedValue(
			new Response(JSON.stringify({ detail: "Authorize again" }), {
				status: 403,
			}),
		);
		await expect(getGitAppSetup()).rejects.toThrow("Authorize again");
	});
});
