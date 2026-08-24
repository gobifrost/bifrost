import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockUseAuthorizationBoundary = vi.fn();

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => mockUseAuthorizationBoundary(),
}));

import { useAdministrativeBoundary } from "./useAdministrativeBoundary";

describe("useAdministrativeBoundary", () => {
	beforeEach(() => {
		mockUseAuthorizationBoundary.mockReturnValue({
			selectedBoundary: "organization:org-home",
			selectedTarget: { boundary: "organization:org-home" },
			targets: [
				{
					boundary: "organization:org-home",
					capabilities: ["organizations.read"],
				},
			],
			hasSelectedCapability: () => true,
		});
	});

	it("uses the explicit context selected by the person", () => {
		expect(
			renderHook(() => useAdministrativeBoundary()).result.current,
		).toBe("organization:org-home");
	});

	it("does not infer a broader context from a default role name", () => {
		mockUseAuthorizationBoundary.mockReturnValue({
			selectedBoundary: "managed_organizations",
			selectedTarget: { boundary: "managed_organizations" },
			targets: [
				{
					boundary: "managed_organizations",
					capabilities: ["organizations.read"],
				},
			],
			hasSelectedCapability: () => true,
		});
		expect(
			renderHook(() => useAdministrativeBoundary()).result.current,
		).toBe("managed_organizations");
	});

	it("never silently replaces the selected context", () => {
		mockUseAuthorizationBoundary.mockReturnValue({
			selectedBoundary: "platform",
			selectedTarget: { boundary: "platform" },
			targets: [
				{ boundary: "platform", capabilities: [] },
				{
					boundary: "organization:org-home",
					capabilities: ["organizations.read"],
				},
			],
			hasSelectedCapability: () => false,
		});
		expect(
			renderHook(() => useAdministrativeBoundary("organizations.read"))
				.result.current,
		).toBe("platform");
	});
});
