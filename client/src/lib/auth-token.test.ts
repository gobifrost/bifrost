import { beforeEach, describe, expect, it } from "vitest";

import { EMBED_TOKEN_KEY, getEmbedTokenClaims } from "./auth-token";

function tokenFor(payload: Record<string, unknown>): string {
	return `header.${btoa(JSON.stringify(payload)).replace(/=/g, "")}.signature`;
}

describe("getEmbedTokenClaims", () => {
	beforeEach(() => sessionStorage.clear());

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
