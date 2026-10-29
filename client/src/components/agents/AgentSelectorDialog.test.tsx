import { AgentSelectorDialog } from "./AgentSelectorDialog";
/**
 * Component tests for AgentSelectorDialog.
 *
 * The dialog fetches the agent + organization lists and renders a
 * searchable single-select list. We mock both hooks at module scope
 * and cover:
 *
 *   - loading state while useAgents is pending
 *   - error state from useAgents
 *   - empty state (no agents)
 *   - agents render with org name badge vs. Global badge
 *   - search filter narrows the list
 *   - selecting an item + clicking Select calls onSelect with the id
 *   - Select is disabled until an item is chosen
 *   - Cancel closes without calling onSelect
 *   - opening the dialog resets search + selection to the incoming prop
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseAgents = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => mockUseAgents(),
}));

const mockUseOrganizations = vi.fn();
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockUseOrganizations(),
}));
const mockUseAuthorization = vi.fn();
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

function makeAgent(overrides: Record<string, unknown> = {}) {
	return {
		id: "agent-1",
		name: "Sales Bot",
		description: "Answers sales questions",
		is_active: true,
		organization_id: null,
		...overrides,
	};
}

beforeEach(() => {
	mockUseAgents.mockReturnValue({
		data: [
			makeAgent({
				id: "agent-1",
				name: "Sales Bot",
				organization_id: null,
			}),
			makeAgent({
				id: "agent-2",
				name: "Support Bot",
				description: "Handles support tickets",
				organization_id: "org-1",
			}),
		],
		isLoading: false,
		error: null,
	});
	mockUseOrganizations.mockReturnValue({
		data: [{ id: "org-1", name: "Acme" }],
	});
	mockUseAuthorization.mockReturnValue({ authorization: undefined });
});

async function renderDialog(
	overrides: Partial<{
		selectedAgentId: string | null;
		onSelect: (id: string) => void;
		onOpenChange: (v: boolean) => void;
	}> = {},
) {

	const onSelect = overrides.onSelect ?? vi.fn();
	const onOpenChange = overrides.onOpenChange ?? vi.fn();
	const utils = renderWithProviders(
		<AgentSelectorDialog
			open={true}
			onOpenChange={onOpenChange}
			selectedAgentId={overrides.selectedAgentId ?? null}
			onSelect={onSelect}
		/>,
	);
	return { ...utils, onSelect, onOpenChange };
}

describe("AgentSelectorDialog — list rendering", () => {
	it("renders each active agent with a Global or org badge", async () => {
		await renderDialog();
		expect(screen.getByText("Sales Bot")).toBeInTheDocument();
		expect(screen.getByText("Support Bot")).toBeInTheDocument();
		expect(screen.getByText("Global")).toBeInTheDocument();
		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("hides inactive agents", async () => {
		mockUseAgents.mockReturnValueOnce({
			data: [
				makeAgent({ id: "agent-1", name: "Active One" }),
				makeAgent({
					id: "agent-2",
					name: "Inactive One",
					is_active: false,
				}),
			],
			isLoading: false,
			error: null,
		});
		await renderDialog();
		expect(screen.getByText("Active One")).toBeInTheDocument();
		expect(screen.queryByText("Inactive One")).not.toBeInTheDocument();
	});
});

describe("AgentSelectorDialog — async states", () => {
	it("shows the loading indicator while useAgents is loading", async () => {
		mockUseAgents.mockReturnValueOnce({
			data: undefined,
			isLoading: true,
			error: null,
		});
		await renderDialog();
		expect(screen.getByText(/loading agents/i)).toBeInTheDocument();
	});

	it("shows the error state when useAgents returns an error", async () => {
		mockUseAgents.mockReturnValueOnce({
			data: undefined,
			isLoading: false,
			error: new Error("fetch failed"),
		});
		await renderDialog();
		expect(screen.getByText(/failed to load agents/i)).toBeInTheDocument();
	});

	it("shows the empty state when no agents exist", async () => {
		mockUseAgents.mockReturnValueOnce({
			data: [],
			isLoading: false,
			error: null,
		});
		await renderDialog();
		expect(screen.getByText(/no agents available/i)).toBeInTheDocument();
	});
});

describe("AgentSelectorDialog — search", () => {
	it("filters the list by the search query", async () => {
		const { user } = await renderDialog();

		await user.type(
			screen.getByPlaceholderText(/search agents/i),
			"support",
		);

		expect(screen.getByText("Support Bot")).toBeInTheDocument();
		expect(screen.queryByText("Sales Bot")).not.toBeInTheDocument();
	});

	it("shows a tailored empty-search state when nothing matches", async () => {
		const { user } = await renderDialog();

		await user.type(
			screen.getByPlaceholderText(/search agents/i),
			"zzz-no-match",
		);

		expect(
			screen.getByText(/no agents match your search/i),
		).toBeInTheDocument();
	});
});

describe("AgentSelectorDialog — selection", () => {
	it("disables Select until an agent is picked, then enables it", async () => {
		const { user } = await renderDialog();

		const selectBtn = screen.getByRole("button", { name: /^select$/i });
		expect(selectBtn).toBeDisabled();

		await user.click(screen.getByText("Support Bot"));

		expect(selectBtn).toBeEnabled();
	});

	it("calls onSelect with the chosen agent id on Select", async () => {
		const { user, onSelect } = await renderDialog();

		await user.click(screen.getByText("Support Bot"));
		await user.click(screen.getByRole("button", { name: /^select$/i }));

		expect(onSelect).toHaveBeenCalledWith("agent-2");
	});

	it("Cancel closes without calling onSelect", async () => {
		const { user, onSelect, onOpenChange } = await renderDialog();

		await user.click(screen.getByText("Support Bot"));
		await user.click(screen.getByRole("button", { name: /cancel/i }));

		expect(onSelect).not.toHaveBeenCalled();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});

it("uses the current selection when a previously closed dialog opens", async () => {

	const onSelect = vi.fn();
	const props = {
		onSelect,
		onOpenChange: vi.fn(),
		selectedAgentId: "agent-1",
	};
	const { rerender, user } = renderWithProviders(
		<AgentSelectorDialog {...props} open={false} />,
	);
	rerender(<AgentSelectorDialog {...props} selectedAgentId="agent-2" open />);
	await user.click(screen.getByRole("button", { name: "Select" }));
	expect(onSelect).toHaveBeenCalledWith("agent-2");
});

it("does not confirm an agent absent from the available list", async () => {
	await renderDialog({ selectedAgentId: "removed-agent" });
	expect(screen.getByRole("button", { name: "Select" })).toBeDisabled();
});
