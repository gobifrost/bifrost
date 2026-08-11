import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Command, CommandGroup, CommandItem, CommandList } from "./command";

const nativeScrollIntoView = Object.getOwnPropertyDescriptor(
	HTMLElement.prototype,
	"scrollIntoView",
);

afterEach(() => {
	if (nativeScrollIntoView) {
		Object.defineProperty(
			HTMLElement.prototype,
			"scrollIntoView",
			nativeScrollIntoView,
		);
	} else {
		Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
	}
});

describe("Command scrolling", () => {
	it("keeps cmdk's selected-item scroll inside the command list", async () => {
		const documentScroll = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: documentScroll,
		});

		render(
			<Command>
				<CommandList>
					<CommandGroup>
						<CommandItem value="one">Item one</CommandItem>
					</CommandGroup>
				</CommandList>
			</Command>,
		);

		const list = screen.getByRole("listbox");
		const item = screen.getByRole("option", { name: "Item one" });
		Object.defineProperty(list, "scrollTop", {
			configurable: true,
			writable: true,
			value: 20,
		});
		vi.spyOn(list, "getBoundingClientRect").mockReturnValue({
			top: 100,
			bottom: 200,
			left: 0,
			right: 200,
			width: 200,
			height: 100,
			x: 0,
			y: 100,
			toJSON: () => ({}),
		});
		const itemRect = vi
			.spyOn(item, "getBoundingClientRect")
			.mockReturnValue({
				top: 210,
				bottom: 240,
				left: 0,
				right: 200,
				width: 200,
				height: 30,
				x: 0,
				y: 210,
				toJSON: () => ({}),
			});

		item.scrollIntoView({ block: "nearest" });

		expect(list.scrollTop).toBe(60);

		itemRect.mockReturnValue({
			top: 70,
			bottom: 100,
			left: 0,
			right: 200,
			width: 200,
			height: 30,
			x: 0,
			y: 70,
			toJSON: () => ({}),
		});
		item.scrollIntoView({ block: "nearest" });

		expect(list.scrollTop).toBe(30);
		await waitFor(() =>
			expect(item).toHaveAttribute("aria-selected", "true"),
		);
		expect(documentScroll).not.toHaveBeenCalled();
	});
});
