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

const { agentsRef, useAgentsMock } = vi.hoisted(() => {
	const agents = { data: [] as Array<Record<string, unknown>> };
	return {
		agentsRef: agents,
		useAgentsMock: vi.fn(() => ({ data: agents.data, isLoading: false, isError: false, isFetching: false, refetch: vi.fn() })),
	};
});

vi.mock("@/hooks/useAgents", () => ({
	useAgents: useAgentsMock,
}));

import { MentionPicker } from "./MentionPicker";

beforeEach(() => {
	useAgentsMock.mockClear();
	agentsRef.data = [
		{
			id: "a-1",
			name: "SupportBot",
			channels: ["chat"],
			description: "answers support tickets",
		},
		{ id: "a-2", name: "DevBot", channels: ["chat"], description: "writes code" },
		{ id: "a-3", name: "DataBot", channels: ["chat"], description: null },
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
		expect(useAgentsMock).toHaveBeenCalledWith(undefined, {
			discoveryOnly: true,
		});
	});
});

describe("MentionPicker — filtering", () => {
	it("excludes non-chat agents even when their names match", () => {
		agentsRef.data.push(
			{ id: "a-4", name: "HiddenBot", channels: [] },
			{ id: "a-5", name: "TeamsBot", channels: ["teams"] },
		);
		renderWithProviders(
			<MentionPicker
				open
				onOpenChange={vi.fn()}
				onSelect={vi.fn()}
				searchTerm="bot"
			/>,
		);
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		expect(screen.queryByText("HiddenBot")).not.toBeInTheDocument();
		expect(screen.queryByText("TeamsBot")).not.toBeInTheDocument();
	});

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


describe("MentionPicker — focused search and recovery", () => {
	it("passes focused search edits to its owner", () => {
		const onSearchChange = vi.fn();
		renderWithProviders(<MentionPicker open onOpenChange={vi.fn()} onSelect={vi.fn()} searchTerm="" onSearchChange={onSearchChange} />);
		fireEvent.change(screen.getByRole("combobox"), { target: { value: "dev" } });
		expect(onSearchChange).toHaveBeenCalledWith("dev");
	});

	it("selects only once when Enter is pressed in the focused search", () => {
		const onSelect = vi.fn();
		renderWithProviders(<MentionPicker open onOpenChange={vi.fn()} onSelect={onSelect} searchTerm="" />);
		fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter", keyCode: 13 });
		expect(onSelect).toHaveBeenCalledTimes(1);
	});

	it("shows loading instead of an empty result", () => {
		useAgentsMock.mockReturnValueOnce({ data: [], isLoading: true, isError: false, isFetching: true, refetch: vi.fn() });
		renderWithProviders(<MentionPicker open onOpenChange={vi.fn()} onSelect={vi.fn()} searchTerm="" />);
		expect(screen.getByRole("status")).toHaveTextContent("Loading agents");
		expect(screen.queryByText(/no agents found/i)).not.toBeInTheDocument();
	});

	it("retains cached choices and retries a failed refresh", () => {
		const refetch = vi.fn();
		useAgentsMock.mockReturnValueOnce({ data: agentsRef.data, isLoading: false, isError: true, isFetching: false, refetch });
		renderWithProviders(<MentionPicker open onOpenChange={vi.fn()} onSelect={vi.fn()} searchTerm="" />);
		expect(screen.getByRole("alert")).toHaveTextContent("Could not refresh agents");
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Retry" }));
		expect(refetch).toHaveBeenCalledTimes(1);
	});
});
