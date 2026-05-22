/**
 * Tests for ConsumerTab — the generic role-consumer tab used by every type
 * except knowledge.
 *
 * Covers: empty state, search filter, multi-select + bulk-unassign callback,
 * Assign button opening the drawer.
 */

import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { ConsumerTab, type ConsumerTabItem } from "./ConsumerTab";

const defaults = {
	items: [] as ConsumerTabItem[],
	isLoading: false,
	candidates: [] as ConsumerTabItem[],
	candidatesLoading: false,
	consumerLabel: "users",
	emptyHint: "No users assigned to this role yet.",
	onAssign: vi.fn().mockResolvedValue(undefined),
	onUnassign: vi.fn().mockResolvedValue(undefined),
};

describe("ConsumerTab", () => {
	it("shows the empty hint when items is empty", () => {
		renderWithProviders(<ConsumerTab {...defaults} />);
		expect(
			screen.getByText("No users assigned to this role yet."),
		).toBeInTheDocument();
	});

	it("renders assigned items", () => {
		renderWithProviders(
			<ConsumerTab
				{...defaults}
				items={[
					{ id: "a", primary: "Alice" },
					{ id: "b", primary: "Bob", secondary: "bob@example.com" },
				]}
			/>,
		);
		expect(screen.getByText("Alice")).toBeInTheDocument();
		expect(screen.getByText("Bob")).toBeInTheDocument();
		expect(screen.getByText("bob@example.com")).toBeInTheDocument();
	});

	it("filters items by search across primary and secondary", async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<ConsumerTab
				{...defaults}
				items={[
					{ id: "a", primary: "Alice", secondary: "alice@example.com" },
					{ id: "b", primary: "Bob", secondary: "bob@example.com" },
				]}
			/>,
		);

		const search = screen.getByPlaceholderText(/search users/i);
		await user.type(search, "bob");

		// SearchBox is debounced — wait for the filter to apply.
		await waitFor(() => {
			expect(screen.queryByText("Alice")).not.toBeInTheDocument();
		});
		expect(screen.getByText("Bob")).toBeInTheDocument();
	});

	it("fires onUnassign with the selected ids", async () => {
		const user = userEvent.setup();
		const onUnassign = vi.fn().mockResolvedValue(undefined);
		renderWithProviders(
			<ConsumerTab
				{...defaults}
				items={[
					{ id: "a", primary: "Alice" },
					{ id: "b", primary: "Bob" },
				]}
				onUnassign={onUnassign}
			/>,
		);

		await user.click(screen.getByLabelText(/Select Alice/));
		await user.click(screen.getByLabelText(/Select Bob/));

		await user.click(
			screen.getByRole("button", { name: /unassign from role/i }),
		);

		await waitFor(() => {
			expect(onUnassign).toHaveBeenCalledOnce();
		});
		const ids = onUnassign.mock.calls[0][0] as string[];
		expect(new Set(ids)).toEqual(new Set(["a", "b"]));
	});

	it("opens the AssignDrawer when the Assign button is clicked", async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<ConsumerTab
				{...defaults}
				candidates={[{ id: "c", primary: "Carol" }]}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /assign users/i }),
		);

		// Candidate is rendered inside the drawer once it opens
		await waitFor(() => {
			expect(screen.getByText("Carol")).toBeInTheDocument();
		});
		// The drawer description identifies it
		expect(
			screen.getByText(/pick the users you want to add/i),
		).toBeInTheDocument();
	});
});
