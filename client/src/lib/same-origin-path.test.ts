import { describe, expect, it } from "vitest";
import { toSameOriginPath } from "./same-origin-path";

const ORIGIN = "https://bifrost.example.com";

describe("toSameOriginPath", () => {
	it("keeps relative paths with their query and hash", () => {
		expect(toSameOriginPath("/workflows?tab=runs#top", ORIGIN)).toBe(
			"/workflows?tab=runs#top",
		);
	});

	it("turns a same-origin absolute URL into a path", () => {
		expect(
			toSameOriginPath(
				`${ORIGIN}/mcp/callback?internal_state=abc-123`,
				ORIGIN,
			),
		).toBe("/mcp/callback?internal_state=abc-123");
	});

	it.each([
		"https://evil.example.com/mcp/callback",
		"http://bifrost.example.com/mcp/callback",
		"//evil.example.com/path",
		"/\\evil.example.com/path",
		"javascript:alert(1)",
	])("falls back to / for %s", (target) => {
		expect(toSameOriginPath(target, ORIGIN)).toBe("/");
	});

	it("falls back to / for empty values", () => {
		expect(toSameOriginPath("", ORIGIN)).toBe("/");
		expect(toSameOriginPath(null, ORIGIN)).toBe("/");
		expect(toSameOriginPath(undefined, ORIGIN)).toBe("/");
	});

	it("defaults to the current window origin", () => {
		expect(toSameOriginPath(`${window.location.origin}/settings?x=1`)).toBe(
			"/settings?x=1",
		);
	});
});
