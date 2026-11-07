import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import { ZeroTrustAutomation } from "./ZeroTrustAutomation";

describe("ZeroTrustAutomation", () => {
	it("opens the platform intent in the shared conversion wizard", () => {
		renderWithProviders(<ZeroTrustAutomation />);
		expect(
			screen.getByText(
				/inherited workflow access can change immediately/i,
			),
		).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Review platform readiness" }),
		).toHaveAttribute("href", "/workflows/convert?platform=true");
	});
});
