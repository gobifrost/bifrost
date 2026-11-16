import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

function renderPopover(arrow?: boolean) {
	return renderWithProviders(
		<Popover defaultOpen>
			<PopoverTrigger>Open</PopoverTrigger>
			<PopoverContent arrow={arrow}>Details</PopoverContent>
		</Popover>,
	);
}

describe("PopoverContent", () => {
	it("points at its trigger when asked for an arrow", () => {
		renderPopover(true);
		const content = screen.getByText("Details");
		expect(
			content.querySelector('[data-slot="popover-arrow"]'),
		).not.toBeNull();
	});

	it("has no arrow by default", () => {
		renderPopover();
		expect(
			document.querySelector('[data-slot="popover-arrow"]'),
		).toBeNull();
	});
});
