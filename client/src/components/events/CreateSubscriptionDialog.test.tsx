/**
 * Component tests for CreateSubscriptionDialog.
 *
 * Covers the target-type branch (workflow vs agent) and the validation /
 * submit payload. The child selector dialogs (WorkflowSelectorDialog,
 * AgentSelectorDialog) are stubbed out.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockCreate = vi.fn();

vi.mock("@/services/events", async () => {
	const actual = await vi.importActual<typeof import("@/services/events")>(
		"@/services/events",
	);
	return {
		...actual,
		useCreateSubscription: () => ({
			mutateAsync: mockCreate,
			isPending: false,
		}),
	};
});

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflows: () => ({
		data: [
			{
				id: "wf-1",
				name: "Onboard",
				parameters: [
					{ name: "ticket_id", type: "int" },
				],
			},
		],
	}),
}));

vi.mock("@/hooks/useAgents", () => ({
	useAgents: () => ({ data: [{ id: "agent-1", name: "Triage Bot" }] }),
}));

// WorkflowSelectorDialog: expose a button that selects wf-1.
vi.mock("@/components/workflows/WorkflowSelectorDialog", () => ({
	WorkflowSelectorDialog: ({
		open,
		onSelect,
	}: {
		open: boolean;
		onSelect: (ids: string[]) => void;
	}) =>
		open ? (
			<button
				type="button"
				onClick={() => onSelect(["wf-1"])}
				data-marker="workflow-selector"
			>
				pick-wf-1
			</button>
		) : null,
}));

vi.mock("@/components/agents/AgentSelectorDialog", () => ({
	AgentSelectorDialog: ({
		open,
		onSelect,
	}: {
		open: boolean;
		onSelect: (id: string) => void;
	}) =>
		open ? (
			<button
				type="button"
				onClick={() => onSelect("agent-1")}
				data-marker="agent-selector"
			>
				pick-agent-1
			</button>
		) : null,
}));

import { CreateSubscriptionDialog } from "./CreateSubscriptionDialog";

beforeEach(() => {
	mockCreate.mockReset();
	mockCreate.mockResolvedValue({});
});

describe("CreateSubscriptionDialog — workflow target", () => {
	it("requires a workflow selection", async () => {
		const { user } = renderWithProviders(
			<CreateSubscriptionDialog
				open
				onOpenChange={() => {}}
				sourceId="src-1"
			/>,
		);

		await user.click(screen.getByRole("button", { name: /add subscription/i }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			/please select a workflow/i,
		);
		expect(mockCreate).not.toHaveBeenCalled();
	});

	it("dispatches a workflow subscription with the input mapping", async () => {
		const { user } = renderWithProviders(
			<CreateSubscriptionDialog
				open
				onOpenChange={() => {}}
				sourceId="src-1"
			/>,
		);

		// Open the workflow selector; our mock surfaces a button that picks wf-1
		await user.click(screen.getByRole("button", { name: /select a workflow/i }));
		await user.click(screen.getByRole("button", { name: /pick-wf-1/i }));

		// After selection, the parameters block is rendered; fill in the mapping.
		fireEvent.change(screen.getByLabelText(/ticket_id/i), {
			target: { value: "{{ payload.id }}" },
		});

		fireEvent.change(screen.getByLabelText(/event type filter/i), {
			target: { value: "ticket.created" },
		});
		await user.click(screen.getByRole("button", { name: /add criteria/i }));
		fireEvent.change(screen.getByLabelText("Criteria field"), {
			target: { value: "event.body.priority" },
		});
		fireEvent.change(screen.getByLabelText("Criteria value"), {
			target: { value: "high" },
		});

		await user.click(screen.getByRole("button", { name: /add subscription/i }));

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		const body = mockCreate.mock.calls[0]![0].body;
		expect(body.target_type).toBe("workflow");
		expect(body.workflow_id).toBe("wf-1");
		expect(body.event_type).toBe("ticket.created");
		expect(body.input_mapping).toEqual({
			ticket_id: "{{ payload.id }}",
		});
		expect(body.criteria).toEqual({
			version: 1,
			root: {
				kind: "all",
				items: [
					{
						kind: "condition",
						field: "event.body.priority",
						operator: "equals",
						value: "high",
					},
				],
			},
		});
	});
});

describe("CreateSubscriptionDialog — agent target", () => {
	it("dispatches an agent subscription", async () => {
		const { user } = renderWithProviders(
			<CreateSubscriptionDialog
				open
				onOpenChange={() => {}}
				sourceId="src-1"
			/>,
		);

		// Radix Select exposes a hidden native <select> for keyboard/a11y —
		// easier to drive than the animated popover in happy-dom.
		const targetSelect = document.querySelectorAll("select")[0];
		expect(targetSelect).toBeTruthy();
		fireEvent.change(targetSelect, { target: { value: "agent" } });

		await user.click(screen.getByRole("button", { name: /select an agent/i }));
		await user.click(screen.getByRole("button", { name: /pick-agent-1/i }));

		await user.click(screen.getByRole("button", { name: /add subscription/i }));

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		const body = mockCreate.mock.calls[0]![0].body;
		expect(body.target_type).toBe("agent");
		expect(body.agent_id).toBe("agent-1");
		// Agents don't receive input_mapping
		expect(body.input_mapping).toBeUndefined();
	});
});
