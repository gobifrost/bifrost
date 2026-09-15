import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "./command";

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

	it("returns a previously scrolled list to the top when search changes", async () => {
		render(
			<Command>
				<CommandInput placeholder="Search workflows" />
				<CommandList>
					<CommandGroup>
						<CommandItem value="workflow-1" keywords={["SharePoint"]}>
							SharePoint site audit
						</CommandItem>
						<CommandItem value="workflow-2" keywords={["Exchange"]}>
							Exchange mailbox audit
						</CommandItem>
					</CommandGroup>
				</CommandList>
			</Command>,
		);

		const list = screen.getByRole("listbox");
		Object.defineProperty(list, "scrollTop", {
			configurable: true,
			writable: true,
			value: 200,
		});

		fireEvent.change(screen.getByPlaceholderText("Search workflows"), {
			target: { value: "sharepoint" },
		});
		await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

		expect(list.scrollTop).toBe(0);
		expect(
			screen.getByRole("option", { name: "SharePoint site audit" }),
		).toBeInTheDocument();
	});
});

describe("Command filtering", () => {
	it("matches every literal query term across item values and keywords", async () => {
		render(
			<Command>
				<CommandInput placeholder="Search workflows" />
				<CommandList>
					<CommandEmpty>No results</CommandEmpty>
					<CommandGroup>
						<CommandItem
							value="workflow-archive"
							keywords={["SharePoint", "Archive document library"]}
						>
							Archive document library
						</CommandItem>
						<CommandItem
							value="workflow-audit"
							keywords={["SharePoint", "Site audit"]}
						>
							SharePoint site audit
						</CommandItem>
					</CommandGroup>
				</CommandList>
			</Command>,
		);

		const input = screen.getByPlaceholderText("Search workflows");
		fireEvent.change(input, { target: { value: "sharepoint archive" } });

		await waitFor(() => {
			expect(
				screen.getByRole("option", { name: "Archive document library" }),
			).toBeInTheDocument();
		});
		expect(
			screen.queryByRole("option", { name: "SharePoint site audit" }),
		).not.toBeInTheDocument();

		fireEvent.change(input, { target: { value: "spt" } });

		await waitFor(() => {
			expect(screen.getByText("No results")).toBeInTheDocument();
		});
	});

	it("preserves an explicit consumer filter", async () => {
		render(
			<Command filter={(value) => (value === "always-visible" ? 1 : 0)}>
				<CommandInput placeholder="Search commands" />
				<CommandList>
					<CommandGroup>
						<CommandItem value="always-visible">Pinned command</CommandItem>
						<CommandItem value="search-match">Search match</CommandItem>
					</CommandGroup>
				</CommandList>
			</Command>,
		);

		fireEvent.change(screen.getByPlaceholderText("Search commands"), {
			target: { value: "search" },
		});

		await waitFor(() => {
			expect(
				screen.getByRole("option", { name: "Pinned command" }),
			).toBeInTheDocument();
		});
		expect(
			screen.queryByRole("option", { name: "Search match" }),
		).not.toBeInTheDocument();
	});

	it("keeps native vertical scrolling available", () => {
		render(
			<Command>
				<CommandList />
			</Command>,
		);

		const list = screen.getByRole("listbox");
		expect(list).toHaveClass("overflow-y-auto");
		expect(list).not.toHaveClass("no-scrollbar");
	});
});
