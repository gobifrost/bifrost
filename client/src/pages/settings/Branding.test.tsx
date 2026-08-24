import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

const brandingApi = vi.hoisted(() => ({
	getBranding: vi.fn(),
}));

vi.mock("@/hooks/useBranding", () => ({
	getBranding: brandingApi.getBranding,
	updateBranding: vi.fn(),
	uploadLogo: vi.fn(),
	resetLogo: vi.fn(),
	resetColor: vi.fn(),
	resetApplicationName: vi.fn(),
}));

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({ refreshBranding: vi.fn() }),
}));

vi.mock("@/lib/branding", () => ({
	applyBrandingTheme: vi.fn(),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

import { Branding } from "./Branding";

describe("Branding", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		brandingApi.getBranding.mockResolvedValue(null);
	});

	it("keeps the standard spacing between field labels and controls", async () => {
		renderWithProviders(<Branding />);

		const name = await screen.findByLabelText("Name");
		expect(name.parentElement).toHaveClass("space-y-2");
		expect(screen.getByLabelText("Color (Hex)").parentElement).toHaveClass(
			"space-y-2",
		);

		for (const input of screen.getAllByLabelText("Singular")) {
			expect(input.parentElement).toHaveClass("space-y-2");
		}
		for (const input of screen.getAllByLabelText("Plural")) {
			expect(input.parentElement).toHaveClass("space-y-2");
		}
	});
});
