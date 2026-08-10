import { beforeEach, describe, expect, it, vi } from "vitest";

import {
	clearPreferredSsoRedirectAttempt,
	getAuthStatus,
	PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY,
} from "./auth";

describe("auth service", () => {
	beforeEach(() => {
		sessionStorage.clear();
		vi.restoreAllMocks();
	});

	it("loads the public login policy", async () => {
		const status = {
			needs_setup: false,
			password_login_enabled: true,
			mfa_required_for_password: false,
			oauth_providers: [],
			auto_redirect_to_sso: true,
			default_sso_provider: "microsoft" as const,
		};
		vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response(JSON.stringify(status), { status: 200 }),
		);

		await expect(getAuthStatus()).resolves.toEqual(status);
		expect(fetch).toHaveBeenCalledWith("/auth/status");
	});

	it("clears the one-attempt guard after authentication", () => {
		sessionStorage.setItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY, "true");

		clearPreferredSsoRedirectAttempt();

		expect(
			sessionStorage.getItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY),
		).toBeNull();
	});
});
