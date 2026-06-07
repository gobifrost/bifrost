import { describe, expect, it } from "vitest";
import { parseSolutionFrom } from "./solution-back-nav";

describe("parseSolutionFrom", () => {
	it("returns the solution id for from=solution:abc", () => {
		expect(parseSolutionFrom("?from=solution:abc")).toBe("abc");
	});

	it("returns null when from is missing", () => {
		expect(parseSolutionFrom("?other=1")).toBeNull();
		expect(parseSolutionFrom("")).toBeNull();
	});

	it("returns null when from is not a solution ref", () => {
		expect(parseSolutionFrom("?from=other")).toBeNull();
		expect(parseSolutionFrom("?from=app:abc")).toBeNull();
	});

	it("returns null for an empty solution id", () => {
		expect(parseSolutionFrom("?from=solution:")).toBeNull();
	});
});
