/**
 * Component tests for HelpSlideout.
 *
 * Verifies the shared help-icon + right-side sheet plumbing:
 *   - Trigger button is labelled by `title`
 *   - Clicking the trigger opens the sheet with title + children visible
 *   - Sheet's built-in Close control closes the sheet again
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { HelpSlideout } from "./HelpSlideout";

describe("HelpSlideout", () => {
	it("renders a trigger button labelled by title", () => {
		renderWithProviders(
			<HelpSlideout title="Policy reference">
				<p>body content</p>
			</HelpSlideout>,
		);
		expect(
			screen.getByRole("button", { name: /policy reference/i }),
		).toBeInTheDocument();
		// Children not shown until opened.
		expect(screen.queryByText(/body content/i)).not.toBeInTheDocument();
	});

	it("opens the sheet with title and children when clicked", async () => {
		const { user } = renderWithProviders(
			<HelpSlideout title="Policy reference">
				<p>body content</p>
			</HelpSlideout>,
		);
		await user.click(
			screen.getByRole("button", { name: /policy reference/i }),
		);
		// Title appears inside the sheet (as a SheetTitle heading).
		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: /policy reference/i }),
			).toBeInTheDocument();
		});
		expect(screen.getByText(/body content/i)).toBeInTheDocument();
	});

	it("closes the sheet when the dismiss control is clicked", async () => {
		const { user } = renderWithProviders(
			<HelpSlideout title="Policy reference">
				<p>body content</p>
			</HelpSlideout>,
		);
		await user.click(
			screen.getByRole("button", { name: /policy reference/i }),
		);
		expect(await screen.findByText(/body content/i)).toBeInTheDocument();

		// SheetContent renders a built-in close button labelled "Close" via sr-only.
		const closeButton = screen.getByRole("button", { name: /^close$/i });
		await user.click(closeButton);

		await waitFor(() => {
			expect(screen.queryByText(/body content/i)).not.toBeInTheDocument();
		});
	});
});
