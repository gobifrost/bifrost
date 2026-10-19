import { beforeEach, describe, expect, it } from "vitest";

import {
	ACCESS_TOKEN_KEY,
	EMBED_TOKEN_KEY,
	IMPERSONATION_TOKEN_KEY,
	clearAuthTokens,
	clearImpersonationToken,
	getActiveToken,
	getImpersonationTokenClaims,
	isImpersonating,
	setImpersonationToken,
	getEmbedTokenClaims,
} from "./auth-token";

function tokenFor(payload: Record<string, unknown>): string {
	return `header.${btoa(JSON.stringify(payload)).replace(/=/g, "")}.signature`;
}

describe("getEmbedTokenClaims", () => {
	beforeEach(() => {
		localStorage.clear();
		sessionStorage.clear();
	});

	it("reads the form binding from this tab's embed token", () => {
		sessionStorage.setItem(
			EMBED_TOKEN_KEY,
			tokenFor({
				embed: true,
				embed_kind: "form",
				grant: "public",
				form_id: "form-1",
			}),
		);

		expect(getEmbedTokenClaims()).toMatchObject({
			embed_kind: "form",
			grant: "public",
			form_id: "form-1",
		});
	});

	it("does not read a token from another storage boundary", () => {
		localStorage.setItem(
			EMBED_TOKEN_KEY,
			tokenFor({ embed_kind: "form", form_id: "wrong-tab" }),
		);
		expect(getEmbedTokenClaims()).toBeNull();
	});

	it("returns null for malformed tokens", () => {
		sessionStorage.setItem(EMBED_TOKEN_KEY, "not-a-jwt");
		expect(getEmbedTokenClaims()).toBeNull();
	});
});

describe("impersonation token storage", () => {
	beforeEach(() => {
		localStorage.clear();
		sessionStorage.clear();
	});

	it("uses a browser-wide impersonation token without replacing the operator token", () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");

		setImpersonationToken("impersonated-token");

		expect(getActiveToken()).toBe("impersonated-token");
		expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe(
			"impersonated-token",
		);
		expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe("operator-token");
		expect(sessionStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBeNull();
		expect(isImpersonating()).toBe(true);
	});

	it("keeps the embed token active while another tab is impersonating", () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
		sessionStorage.setItem(EMBED_TOKEN_KEY, "embed-token");

		setImpersonationToken("impersonated-token");

		expect(getActiveToken()).toBe("embed-token");
		expect(isImpersonating()).toBe(false);
		expect(getImpersonationTokenClaims()).toBeNull();
	});

	it("clears the browser-wide impersonation session", () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
		setImpersonationToken("impersonated-token");

		clearImpersonationToken();

		expect(getActiveToken()).toBe("operator-token");
		expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe("operator-token");
		expect(isImpersonating()).toBe(false);
	});

	it("does not end another tab's support session when an embed clears its capability", () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
		sessionStorage.setItem(EMBED_TOKEN_KEY, "embed-token");
		setImpersonationToken("impersonated-token");

		clearAuthTokens();

		expect(sessionStorage.getItem(EMBED_TOKEN_KEY)).toBeNull();
		expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe("operator-token");
		expect(localStorage.getItem(IMPERSONATION_TOKEN_KEY)).toBe(
			"impersonated-token",
		);
	});
});
