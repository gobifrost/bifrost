import { renderWithProviders, screen } from "@/test-utils";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/services/ai-pricing", () => ({
	listPricing: vi.fn().mockResolvedValue({ pricing: [], models_without_pricing: ["unpriced-model"] }),
	createPricing: vi.fn(),
	updatePricing: vi.fn(),
	deletePricing: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AIUsageSettings } from "./AIUsageSettings";

describe("AIUsageSettings", () => {
	it("surfaces missing rates and opens the reusable pricing form", async () => {
		const user = userEvent.setup();
		renderWithProviders(<AIUsageSettings />);

		expect(await screen.findByText("unpriced-model")).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Add pricing" }));
		expect(screen.getByRole("dialog")).toBeVisible();
		expect(screen.getByLabelText("Provider")).toBeVisible();
	});
});
