/**
 * Component tests for DynamicConfigForm.
 *
 * Covers each static field-type branch (string, boolean, static enum, array
 * enum toggle group). Dynamic values with x-dynamic-values would require
 * hitting the useDynamicValues hook; we stub that hook with empty data so
 * the dependency-satisfied text-input branch renders deterministically.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";

type DynamicValuesMockResult = {
	data?: { items: Record<string, unknown>[] };
	isLoading: boolean;
	error: Error | null;
	refetch: ReturnType<typeof vi.fn>;
	isFetching: boolean;
};

const mockUseDynamicValues = vi.fn<
	(...args: unknown[]) => DynamicValuesMockResult
>(() => ({
	data: { items: [] },
	isLoading: false,
	error: null,
	refetch: vi.fn(),
	isFetching: false,
}));

vi.mock("@/services/events", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/events")>(
			"@/services/events",
		);
	return {
		...actual,
		useDynamicValues: (...args: unknown[]) => mockUseDynamicValues(...args),
	};
});

import { DynamicConfigForm, type ConfigSchema } from "./DynamicConfigForm";

beforeEach(() => {
	mockUseDynamicValues.mockReset();
	mockUseDynamicValues.mockReturnValue({
		data: { items: [] },
		isLoading: false,
		error: null,
		refetch: vi.fn(),
		isFetching: false,
	});
});

function renderForm(
	schema: ConfigSchema,
	config: Record<string, unknown> = {},
) {
	const onChange = vi.fn();
	const utils = renderWithProviders(
		<DynamicConfigForm
			adapterName="test-adapter"
			organizationId="org-1"
			configSchema={schema}
			config={config}
			onChange={onChange}
		/>,
	);
	return { ...utils, onChange };
}

describe("DynamicConfigForm — empty schema", () => {
	it("renders nothing when the schema has no properties", () => {
		const { container } = renderForm({ type: "object", properties: {} });
		expect(container.firstChild).toBeNull();
	});
});

describe("DynamicConfigForm — string field", () => {
	it("renders a text input and emits typed string via onChange", () => {
		const { onChange } = renderForm({
			type: "object",
			properties: {
				label: { type: "string", title: "Label" },
			},
		});

		fireEvent.change(screen.getByLabelText(/label/i), {
			target: { value: "foo" },
		});

		expect(onChange).toHaveBeenLastCalledWith({ label: "foo" });
	});

	it("removes the key from config when the input is cleared", () => {
		const { onChange } = renderForm(
			{
				type: "object",
				properties: { label: { type: "string", title: "Label" } },
			},
			{ label: "existing" },
		);

		fireEvent.change(screen.getByLabelText(/label/i), {
			target: { value: "" },
		});

		expect(onChange).toHaveBeenLastCalledWith({});
	});
});

describe("DynamicConfigForm — boolean field", () => {
	it("renders a checkbox and emits booleans", async () => {
		const { user, onChange } = renderForm({
			type: "object",
			properties: {
				enabled: { type: "boolean", title: "Enabled" },
			},
		});

		await user.click(screen.getByRole("checkbox", { name: /enabled/i }));

		expect(onChange).toHaveBeenLastCalledWith({ enabled: true });
	});
});

describe("DynamicConfigForm — static enum", () => {
	it("renders the selectable options from the enum", () => {
		renderForm({
			type: "object",
			properties: {
				mode: {
					type: "string",
					title: "Mode",
					enum: ["live", "test"],
				},
			},
		});
		// The closed select shows a placeholder containing the title
		expect(
			screen.getByRole("combobox", { name: /mode/i }),
		).toBeInTheDocument();
	});
});

describe("DynamicConfigForm — required markers", () => {
	it("renders a required marker next to required fields", () => {
		renderForm({
			type: "object",
			required: ["label"],
			properties: {
				label: { type: "string", title: "Label" },
			},
		});
		expect(screen.getByText("*")).toBeInTheDocument();
	});
});

describe("DynamicConfigForm — dynamic values", () => {
	it("passes organization context to dynamic value requests", () => {
		renderForm({
			type: "object",
			properties: {
				user_id: {
					type: "string",
					title: "User",
					"x-dynamic-values": {
						operation: "list_users",
						value_path: "id",
						label_path: "display_name",
						depends_on: [],
					},
				},
			},
		});

		expect(mockUseDynamicValues).toHaveBeenLastCalledWith(
			"test-adapter",
			"list_users",
			undefined,
			"org-1",
			{},
			true,
		);
	});

	it("shows a retry action when dynamic options fail to load", async () => {
		const refetch = vi.fn();
		mockUseDynamicValues.mockReturnValueOnce({
			data: undefined,
			isLoading: false,
			error: new Error("No tenant mapping was found"),
			refetch,
			isFetching: false,
		});

		const { user } = renderForm(
			{
				type: "object",
				properties: {
					user_id: {
						type: "string",
						title: "User",
						"x-dynamic-values": {
							operation: "list_users",
							value_path: "id",
							label_path: "display_name",
							depends_on: [],
						},
					},
				},
			},
			{ user_id: "user@example.com" },
		);

		expect(screen.getByRole("status")).toHaveTextContent(
			/No tenant mapping was found/i,
		);
		expect(screen.getByLabelText(/user/i)).toHaveValue("user@example.com");

		await user.click(screen.getByRole("button", { name: /retry/i }));

		expect(refetch).toHaveBeenCalledTimes(1);
	});

	it("shows FastAPI error details instead of the generic fallback", () => {
		mockUseDynamicValues.mockReturnValueOnce({
			data: undefined,
			isLoading: false,
			error: {
				detail:
					"Integration 'Microsoft' is not mapped to the selected organization",
			} as unknown as Error,
			refetch: vi.fn(),
			isFetching: false,
		});

		renderForm({
			type: "object",
			properties: {
				user_id: {
					type: "string",
					title: "User",
					"x-dynamic-values": {
						operation: "list_users",
						value_path: "id",
						label_path: "display_name",
						depends_on: [],
					},
				},
			},
		});

		expect(screen.getByRole("status")).toHaveTextContent(
			/Integration 'Microsoft' is not mapped to the selected organization/i,
		);
	});
});
