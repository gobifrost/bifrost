import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockUseAuthorization } = vi.hoisted(() => ({
	mockUseAuthorization: vi.fn(),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

import {
	resolveOrganizationName,
	useOrganizationName,
} from "./useOrganizationName";

describe("resolveOrganizationName", () => {
	beforeEach(() => {
		mockUseAuthorization.mockReset();
	});
	it("uses the caller's home organization name when the directory omits it", () => {
		expect(
			resolveOrganizationName(
				"00000000-0000-0000-0000-000000000002",
				[],
				"00000000-0000-0000-0000-000000000002",
				"Covi",
			),
		).toBe("Covi");
	});

	it("keeps directory names, Global, and unknown identifiers intact", () => {
		const organizations = [{ id: "customer", name: "Managed Customer" }];

		expect(
			resolveOrganizationName("customer", organizations, "home", "Covi"),
		).toBe("Managed Customer");
		expect(resolveOrganizationName(null, organizations, "home", "Covi")).toBe(
			"Global",
		);
		expect(
			resolveOrganizationName("unknown", organizations, "home", "Covi"),
		).toBe("unknown");
		expect(
			resolveOrganizationName(
				"unknown",
				organizations,
				"home",
				"Covi",
				"Unknown organization",
			),
		).toBe("Unknown organization");
	});

	it("updates only the home label when authorization becomes available", () => {
		mockUseAuthorization.mockReturnValue({ authorization: undefined });
		const { result, rerender } = renderHook(() => useOrganizationName([]));

		expect(result.current("home")).toBe("home");
		expect(result.current("foreign")).toBe("foreign");

		mockUseAuthorization.mockReturnValue({
			authorization: {
				home_organization_id: "home",
				home_organization_name: "Covi",
			},
		});
		rerender();

		expect(result.current("home")).toBe("Covi");
		expect(result.current("foreign")).toBe("foreign");
	});
});
