/**
 * Component tests for CreateIntegrationDialog.
 *
 * Covers:
 *   - create mode: name required -> submit dispatches mutation
 *   - edit mode with name change -> confirmation dialog must be confirmed
 *     before the update fires
 *
 * We mock the three hooks (useCreateIntegration, useUpdateIntegration,
 * useIntegration, useDataProviders) at the module level so the component
 * doesn't touch the network. The Combobox for data providers is exercised
 * only indirectly — we skip validating the combobox UI itself.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockCreate = vi.fn();
const mockUpdate = vi.fn();
let mockIntegration: unknown = undefined;

vi.mock("@/services/integrations", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/integrations")>(
			"@/services/integrations",
		);
	return {
		...actual,
		useCreateIntegration: () => ({
			mutateAsync: mockCreate,
			isPending: false,
		}),
		useUpdateIntegration: () => ({
			mutateAsync: mockUpdate,
			isPending: false,
		}),
		useIntegration: () => ({
			data: mockIntegration,
			isLoading: false,
			dataUpdatedAt: 1,
		}),
	};
});

vi.mock("@/services/dataProviders", () => ({
	useDataProviders: () => ({ data: [], isLoading: false }),
}));

import { CreateIntegrationDialog } from "./CreateIntegrationDialog";

beforeEach(() => {
	mockCreate.mockReset();
	mockCreate.mockResolvedValue({});
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue({});
	mockIntegration = undefined;
});

describe("CreateIntegrationDialog — create mode", () => {
	it("dispatches createMutation with the entered name", async () => {
		const { user } = renderWithProviders(
			<CreateIntegrationDialog open onOpenChange={() => {}} />,
		);

		const nameInput = screen.getByLabelText(/integration name/i);
		fireEvent.change(nameInput, { target: { value: "Slack" } });

		await user.click(
			screen.getByRole("button", { name: /create integration/i }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		const payload = mockCreate.mock.calls[0]![0];
		expect(payload.body.name).toBe("Slack");
	});
});

describe("CreateIntegrationDialog — edit mode", () => {
	it("prompts for confirmation when the name changes and only dispatches after confirm", async () => {
		mockIntegration = {
			id: "int-1",
			name: "Original",
			config_schema: [],
			list_entities_data_provider_id: null,
			default_entity_id: "",
		};

		const { user } = renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		const nameInput = screen.getByLabelText(/integration name/i);
		fireEvent.change(nameInput, { target: { value: "New Name" } });

		await user.click(
			screen.getByRole("button", { name: /update integration/i }),
		);

		// A confirmation alert should appear — update has NOT been dispatched yet.
		expect(mockUpdate).not.toHaveBeenCalled();
		expect(
			screen.getByRole("heading", { name: /rename integration/i }),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /rename anyway/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const payload = mockUpdate.mock.calls[0]![0];
		expect(payload.params.path.integration_id).toBe("int-1");
		expect(payload.params.query.force_remove_keys).toBe(false);
		expect(payload.body.name).toBe("New Name");
	});

	it("sends destructive schema confirmation to REST and can remove the final field", async () => {
		mockIntegration = {
			id: "int-1",
			name: "Original",
			config_schema: [
				{
					key: "api_key",
					type: "secret",
					required: true,
				},
			],
			list_entities_data_provider_id: null,
			default_entity_id: "",
		};

		const { user } = renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /remove api_key/i }),
		);
		await user.click(
			screen.getByRole("button", { name: /update integration/i }),
		);

		expect(mockUpdate).not.toHaveBeenCalled();
		expect(
			screen.getByRole("heading", {
				name: /remove configuration fields/i,
			}),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /delete fields/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const payload = mockUpdate.mock.calls[0]![0];
		expect(payload.params.query.force_remove_keys).toBe(true);
		expect(payload.body.config_schema).toEqual([]);
	});
});
