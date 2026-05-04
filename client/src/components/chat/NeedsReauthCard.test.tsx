/**
 * Coverage for the pure ``extractNeedsReauth`` helper. The component
 * itself wraps OAuth/popup behavior that's better exercised in
 * Playwright; this file just locks the contract in the helper.
 */

import { describe, it, expect } from "vitest";

import { extractNeedsReauth } from "./NeedsReauthCard";

describe("extractNeedsReauth", () => {
	it("returns null for non-objects", () => {
		expect(extractNeedsReauth(null)).toBeNull();
		expect(extractNeedsReauth(undefined)).toBeNull();
		expect(extractNeedsReauth("oops")).toBeNull();
		expect(extractNeedsReauth(42)).toBeNull();
	});

	it("returns null when error_type is not needs_reauth", () => {
		expect(
			extractNeedsReauth({
				error_type: "other",
				metadata: { connection_id: "abc" },
			}),
		).toBeNull();
	});

	it("returns metadata when shape matches", () => {
		const out = extractNeedsReauth({
			error_type: "needs_reauth",
			metadata: {
				connection_id: "conn-1",
				reauth_url: "/api/me/mcp-connections/conn-1/connect",
				tool_name: "graph_search",
			},
		});
		expect(out).toEqual({
			connection_id: "conn-1",
			reauth_url: "/api/me/mcp-connections/conn-1/connect",
			tool_name: "graph_search",
		});
	});

	it("tolerates missing metadata fields", () => {
		const out = extractNeedsReauth({ error_type: "needs_reauth" });
		expect(out).toEqual({
			connection_id: undefined,
			reauth_url: undefined,
			tool_name: undefined,
		});
	});
});
