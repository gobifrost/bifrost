/**
 * Component tests for MentionPicker.
 *
 * Covers:
 *   - Returns null when closed
 *   - Filters agents by search term (case-insensitive, matches description too)
 *   - Clicking an item invokes onSelect with the agent
 *   - ArrowDown / Enter selects via keyboard
 *   - Escape closes the picker
 *
 * useAgents is mocked at the module level to return a fixed list.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";

const agentsRef: { data: Array<Record<string, unknown>> } = { data: [] };

vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => ({ data: agentsRef.data }),
}));

import { MentionPicker } from "./MentionPicker";

beforeEach(() => {
	agentsRef.data = [
		{
			id: "a-1",
			name: "SupportBot",
			description: "answers support tickets",
		},
		{ id: "a-2", name: "DevBot", description: "writes code" },
		{ id: "a-3", name: "DataBot", description: null },
	];
});

describe("MentionPicker — visibility", () => {
	it("renders nothing when closed", () => {
		const { container } = renderWithProviders(
			<MentionPicker
				open={false}
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm=""
			/>,
		);
		expect(container.firstChild).toBeNull();
	});

	it("renders all agents when open with an empty search term", () => {
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm=""
			/>,
		);
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		expect(screen.getByText("DevBot")).toBeInTheDocument();
		expect(screen.getByText("DataBot")).toBeInTheDocument();
	});
});

describe("MentionPicker — filtering", () => {
	it("filters by name match (case-insensitive)", () => {
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm="dev"
			/>,
		);
		expect(screen.getByText("DevBot")).toBeInTheDocument();
		expect(screen.queryByText("SupportBot")).not.toBeInTheDocument();
		expect(screen.queryByText("DataBot")).not.toBeInTheDocument();
	});

	it("description-only matches stay visible because the picker filters them itself", () => {
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm="tickets"
			/>,
		);
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		expect(
			screen.getByText("SupportBot").closest("[cmdk-item]"),
		).toHaveAttribute("data-checked", "true");
	});

	it("resets the highlighted row when the search term changes", () => {
		const view = renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "ArrowDown" });
		const highlightedItem = screen
			.getByText("DevBot")
			.closest("[cmdk-item]");
		expect(highlightedItem).toHaveAttribute("data-checked", "true");
		expect(highlightedItem).toHaveAttribute("data-selected", "true");

		view.rerender(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm="bot"
			/>,
		);

		expect(
			screen.getByText("SupportBot").closest("[cmdk-item]"),
		).toHaveAttribute("data-checked", "true");
	});

	it("shows the empty-state when nothing matches", () => {
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm="zzzz"
			/>,
		);
		expect(screen.getByText(/no agents found/i)).toBeInTheDocument();
	});
});

describe("MentionPicker — selection & keyboard", () => {
	it("fires onSelect with the clicked agent", async () => {
		const onSelect = vi.fn();
		const { user } = renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={onSelect}
				searchTerm=""
			/>,
		);

		await user.click(screen.getByText("DevBot"));

		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({
			id: "a-2",
			name: "DevBot",
		});
	});

	it("Enter selects the highlighted item (starts at index 0)", () => {
		const onSelect = vi.fn();
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={onSelect}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "Enter" });

		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: "a-1" });
	});

	it("Tab selects the highlighted item (starts at index 0)", () => {
		const onSelect = vi.fn();
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={onSelect}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "Tab" });

		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: "a-1" });
	});

	it("ArrowDown moves the highlight, then Tab selects the new row", () => {
		const onSelect = vi.fn();
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={onSelect}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "ArrowDown" });
		fireEvent.keyDown(window, { key: "Tab" });

		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: "a-2" });
	});

	it("ArrowDown moves the highlight, then Enter selects the new row", () => {
		const onSelect = vi.fn();
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={onSelect}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "ArrowDown" });
		fireEvent.keyDown(window, { key: "Enter" });

		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: "a-2" });
	});

	it("Escape invokes onOpenChange(false)", () => {
		const onOpenChange = vi.fn();
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={onOpenChange}
				onSelect={vi.fn()}
				searchTerm=""
			/>,
		);

		fireEvent.keyDown(window, { key: "Escape" });

		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});
