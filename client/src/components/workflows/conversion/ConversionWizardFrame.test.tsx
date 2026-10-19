import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ConversionWizardFrame } from "./ConversionWizardFrame";

describe("ConversionWizardFrame", () => {
	it("exposes completed, current and upcoming steps around one task and footer", () => {
		render(
			<ConversionWizardFrame
				stage="review"
				platformIntent={false}
				footer={<button>Review selected</button>}
			>
				<h2>Review proposals</h2>
			</ConversionWizardFrame>,
		);
		const steps = within(
			screen.getByRole("list", { name: "Conversion steps" }),
		).getAllByRole("listitem");
		expect(steps).toHaveLength(4);
		expect(steps[0]).toHaveAccessibleName("Confirm scope, complete");
		expect(steps[2]).toHaveAccessibleName("Review, current");
		expect(steps[2]).toHaveAttribute("aria-current", "step");
		expect(steps[3]).toHaveAccessibleName("Apply, upcoming");
		expect(
			screen.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		expect(
			screen
				.getByRole("button", { name: "Review selected" })
				.closest("footer"),
		).not.toBeNull();
	});
	it("represents the separate platform-default stage only for Settings flows", () => {
		render(
			<ConversionWizardFrame
				stage="cutover"
				platformIntent
				footer={<button>Change default</button>}
			>
				<h2>Platform cutover</h2>
			</ConversionWizardFrame>,
		);
		const steps = within(
			screen.getByRole("list", { name: "Conversion steps" }),
		).getAllByRole("listitem");
		expect(steps).toHaveLength(5);
		expect(steps[4]).toHaveAccessibleName("Platform default, current");
		expect(steps[4]).toHaveAttribute("aria-current", "step");
	});
});
