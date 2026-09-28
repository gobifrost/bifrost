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
const mockIntegrationRefetch = vi.fn();
const mockDataProvidersRefetch = vi.fn();
let mockIntegration: unknown = undefined;
let mockDataProviders:
	| {
			data?: Array<{ id?: string | null; name: string }>;
			isLoading: boolean;
			isError: boolean;
			isFetching: boolean;
			refetch: typeof mockDataProvidersRefetch;
	  }
	| undefined;

vi.mock("@/services/integrations", async () => {
	const actual = await vi.importActual<
		typeof import("@/services/integrations")
	>("@/services/integrations");
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
			isFetching: false,
			refetch: mockIntegrationRefetch,
			dataUpdatedAt: 1,
		}),
	};
});

vi.mock("@/services/dataProviders", () => ({
	useDataProviders: () =>
		mockDataProviders ?? {
			data: [],
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: mockDataProvidersRefetch,
		},
}));

import { CreateIntegrationDialog } from "./CreateIntegrationDialog";

beforeEach(() => {
	mockCreate.mockReset();
	mockCreate.mockResolvedValue({});
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue({});
	mockIntegration = undefined;
	mockIntegrationRefetch.mockReset();
	mockDataProvidersRefetch.mockReset();
	mockDataProviders = undefined;
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

	it("submits the exact create payload and retries with the same draft after a failure", async () => {
		mockCreate
			.mockRejectedValueOnce(new Error("Synthetic create failure"))
			.mockResolvedValueOnce({});

		const { user } = renderWithProviders(
			<CreateIntegrationDialog open onOpenChange={() => {}} />,
		);

		await user.type(
			screen.getByLabelText(/integration name/i),
			"Synthetic integration",
		);
		await user.type(screen.getByLabelText(/default entity id/i), "common");
		await user.click(screen.getByRole("button", { name: /add field/i }));
		await user.type(screen.getByLabelText(/field key/i), "tenant_id");

		await user.click(
			screen.getByRole("button", { name: /create integration/i }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		expect(mockCreate.mock.calls[0]![0].body).toEqual({
			name: "Synthetic integration",
			description: null,
			config_schema: [
				{
					key: "tenant_id",
					type: "string",
					required: false,
				},
			],
			default_entity_id: "common",
		});
		expect(await screen.findByRole("alert")).toHaveTextContent(
			/your changes are preserved/i,
		);
		expect(screen.getByLabelText(/integration name/i)).toHaveValue(
			"Synthetic integration",
		);
		expect(screen.getByLabelText(/default entity id/i)).toHaveValue(
			"common",
		);

		await user.click(
			screen.getByRole("button", { name: /create integration/i }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));
		expect(mockCreate.mock.calls[1]![0].body).toEqual(
			mockCreate.mock.calls[0]![0].body,
		);
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

		await user.click(
			screen.getByRole("button", { name: /rename anyway/i }),
		);

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const payload = mockUpdate.mock.calls[0]![0];
		expect(payload.params.path.integration_id).toBe("int-1");
		expect(payload.body.name).toBe("New Name");
	});

	it("retries a failed initial load, then preserves the draft through a background refresh", async () => {
		const initialIntegration = {
			id: "int-1",
			name: "Original",
			config_schema: [],
			list_entities_data_provider_id: null,
			default_entity_id: "",
		};
		const refreshedIntegration = {
			...initialIntegration,
			name: "Server refresh",
		};
		mockIntegration = undefined;
		mockIntegrationRefetch.mockImplementation(() => {
			mockIntegration = initialIntegration;
		});

		const { user, rerender } = renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /retry integration/i }),
		);
		expect(mockIntegrationRefetch).toHaveBeenCalledTimes(1);
		rerender(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		const nameInput = screen.getByLabelText(/integration name/i);
		expect(nameInput).toHaveValue("Original");
		await user.clear(nameInput);
		await user.type(nameInput, "Draft name");

		mockIntegration = refreshedIntegration;
		rerender(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		expect(screen.getByLabelText(/integration name/i)).toHaveValue(
			"Draft name",
		);
	});

	it("retries a failed data provider read without losing the edit draft", async () => {
		mockIntegration = {
			id: "int-1",
			name: "Original",
			config_schema: [],
			list_entities_data_provider_id: null,
			default_entity_id: "",
		};
		mockDataProviders = {
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch: mockDataProvidersRefetch,
		};
		mockDataProvidersRefetch.mockImplementation(() => {
			mockDataProviders = {
				data: [{ id: "provider-1", name: "Provider One" }],
				isLoading: false,
				isError: false,
				isFetching: false,
				refetch: mockDataProvidersRefetch,
			};
		});

		const { user, rerender } = renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		const alert = screen.getByRole("alert");
		expect(alert).toHaveTextContent(/unable to load data providers/i);
		await user.click(
			screen.getByRole("button", { name: /retry data providers/i }),
		);
		expect(mockDataProvidersRefetch).toHaveBeenCalledTimes(1);
		rerender(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		expect(
			screen.getByRole("combobox", { name: /entity data provider/i }),
		).toBeVisible();
		expect(screen.getByLabelText(/integration name/i)).toHaveValue(
			"Original",
		);
		await user.clear(screen.getByLabelText(/integration name/i));
		await user.type(
			screen.getByLabelText(/integration name/i),
			"Draft name",
		);

		mockDataProviders = {
			data: [{ id: "provider-2", name: "Provider Two" }],
			isLoading: false,
			isError: false,
			isFetching: true,
			refetch: mockDataProvidersRefetch,
		};
		rerender(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);

		expect(screen.getByLabelText(/integration name/i)).toHaveValue(
			"Draft name",
		);
	});
});

describe("CreateIntegrationDialog — save recovery", () => {
	it("blocks dismissal while saving and retains the draft after failure for retry", async () => {
		let rejectSave!: (error: Error) => void;
		mockCreate.mockImplementationOnce(
			() =>
				new Promise((_resolve, reject) => {
					rejectSave = reject;
				}),
		);
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<CreateIntegrationDialog open onOpenChange={onOpenChange} />,
		);
		const name = screen.getByLabelText(/integration name/i);
		fireEvent.change(name, { target: { value: "Draft integration" } });
		await user.click(
			screen.getByRole("button", { name: /create integration/i }),
		);
		expect(name).toBeDisabled();
		await user.keyboard("{Escape}");
		expect(onOpenChange).not.toHaveBeenCalled();
		rejectSave(new Error("Synthetic save failure"));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Your changes are preserved",
		);
		expect(name).toHaveValue("Draft integration");
		expect(name).toBeEnabled();
		await user.click(
			screen.getByRole("button", { name: /create integration/i }),
		);
		await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
		expect(mockCreate).toHaveBeenCalledTimes(2);
	});
});

describe("CreateIntegrationDialog — edit safeguards", () => {
	it("shows the server's 409 field-removal confirmation, then retries with force_remove_keys", async () => {
		mockIntegration = {
			id: "int-1",
			name: "Original",
			config_schema: [
				{ key: "removed_key", type: "string", required: false },
			],
		};
		// First call (no force flag) — server refuses because the dropped
		// key has a saved Config value. Second call (force_remove_keys=true)
		// on the confirm dialog's "Delete Fields" — succeeds.
		mockUpdate.mockRejectedValueOnce({
			detail: {
				code: "integration_schema_removal_requires_confirmation",
				removed_keys: ["removed_key"],
				affected_config_values: 2,
			},
		});
		mockUpdate.mockResolvedValueOnce({});

		const { user } = renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);
		fireEvent.change(screen.getByLabelText(/integration name/i), {
			target: { value: "Renamed" },
		});
		await user.click(
			screen.getByRole("button", { name: "Remove field 1" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Update Integration" }),
		);
		await user.click(screen.getByRole("button", { name: "Rename Anyway" }));

		// The rename-confirmed save fires immediately and hits the server's
		// 409 — no client-side guess about which keys have saved values.
		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		expect(mockUpdate.mock.calls[0][0].params.query).toEqual({
			force_remove_keys: false,
		});
		expect(
			await screen.findByRole("heading", {
				name: "Remove Configuration Fields?",
			}),
		).toBeInTheDocument();
		expect(screen.getByText(/2 saved config values/)).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Delete Fields" }));
		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(2));
		expect(mockUpdate.mock.calls[1][0].params.query).toEqual({
			force_remove_keys: true,
		});
		expect(mockUpdate.mock.calls[1][0].body.config_schema).toEqual([]);
	});

	it("does not expose an empty edit form when loading has failed", () => {
		renderWithProviders(
			<CreateIntegrationDialog
				open
				onOpenChange={() => {}}
				editIntegrationId="int-1"
			/>,
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Unable to load integration",
		);
		expect(
			screen.getByRole("button", { name: "Retry integration" }),
		).toBeInTheDocument();
		expect(
			screen.queryByLabelText(/integration name/i),
		).not.toBeInTheDocument();
	});
});
