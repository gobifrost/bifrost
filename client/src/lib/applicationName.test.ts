import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import {
	DEFAULT_APPLICATION_NAME,
	resolveApplicationName,
	useApplicationName,
} from "./applicationName";

// Mock the org-scope context so the hook can be exercised without a provider.
const mockUseOrgScope = vi.fn();
vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => mockUseOrgScope(),
}));

describe("resolveApplicationName", () => {
	it("returns the custom name when set", () => {
		expect(resolveApplicationName("Acme Portal")).toBe("Acme Portal");
	});

	it("trims surrounding whitespace", () => {
		expect(resolveApplicationName("  Acme  ")).toBe("Acme");
	});

	it.each([null, undefined, "", "   "])(
		"falls back to the default for %p",
		(value) => {
			expect(resolveApplicationName(value as string | null)).toBe(
				DEFAULT_APPLICATION_NAME,
			);
		},
	);
});

describe("useApplicationName", () => {
	it("returns the custom branding name when present", () => {
		mockUseOrgScope.mockReturnValue({ applicationName: "Acme Portal" });
		const { result } = renderHook(() => useApplicationName());
		expect(result.current).toBe("Acme Portal");
	});

	it("returns the default when branding has no name", () => {
		mockUseOrgScope.mockReturnValue({ applicationName: null });
		const { result } = renderHook(() => useApplicationName());
		expect(result.current).toBe(DEFAULT_APPLICATION_NAME);
	});
});
