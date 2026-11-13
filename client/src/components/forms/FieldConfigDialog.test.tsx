/**
 * Component tests for FieldConfigDialog.
 *
 * Focus on the most load-bearing behavior:
 * - field-name validation: Python keyword and invalid-character errors
 * - field-name auto-formatting: spaces → underscores, lowercased
 * - label auto-generation from field name
 * - Save button disabled when name or label is empty
 * - conditional rendering of Options editor when type=radio or select without provider
 * - save payload for a basic text field
 *
 * We stub the Monaco Editor, ExpressionEditor, ContextViewer, and the
 * DataProviders service to keep the test fast.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (query: string) => query === "(max-width: 1023px)",
}));

// Monaco editor ("@monaco-editor/react") — huge and slow to load; stub it.
vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
	}) => (
		<textarea
			aria-label="monaco"
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

// ExpressionEditor + validateExpression — the dialog validates on save.
vi.mock("@/components/ui/expression-editor", () => ({
	ExpressionEditor: ({
		value,
		onChange,
		label,
	}: {
		value: string;
		onChange: (v: string) => void;
		label?: string;
	}) => (
		<label>
			{label || "expression"}
			<textarea
				aria-label={label || "expression"}
				value={value}
				onChange={(e) => onChange(e.target.value)}
			/>
		</label>
	),
	validateExpression: (expr: string) => ({
		isValid: !expr.includes("INVALID"),
		error: expr.includes("INVALID") ? "bad expression" : undefined,
	}),
}));

vi.mock("@/components/ui/context-viewer", () => ({
	ContextViewer: () => <div />,
}));

// Data providers service — default to an empty list.
vi.mock("@/services/dataProviders", () => ({
	useDataProviders: () => ({ data: [], isLoading: false }),
}));

import { FieldConfigDialog } from "./FieldConfigDialog";
import { ThemeProvider } from "@/contexts/ThemeContext";

function renderDialog(overrides: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onSave = vi.fn();
	const utils = renderWithProviders(
		<ThemeProvider>
			<FieldConfigDialog
				open={true}
				onClose={onClose}
				onSave={onSave}
				{...overrides}
			/>
		</ThemeProvider>,
	);
	return { ...utils, onClose, onSave };
}

describe("FieldConfigDialog — field-name validation", () => {
	it("shows an error when the field name is a Python keyword", async () => {
		const { user } = renderDialog();

		const nameInput = screen.getByLabelText(/field name/i);
		await user.type(nameInput, "class");

		expect(
			screen.getByText(/must start with letter\/underscore/i),
		).toBeInTheDocument();
	});

	it("shows an error when the field name starts with a digit", async () => {
		const { user } = renderDialog();

		const nameInput = screen.getByLabelText(/field name/i);
		await user.type(nameInput, "1bad");

		expect(
			screen.getByText(/must start with letter\/underscore/i),
		).toBeInTheDocument();
	});

	it("normalizes spaces to underscores and lowercases the field name", async () => {
		const { user } = renderDialog();

		const nameInput = screen.getByLabelText(/field name/i);
		await user.type(nameInput, "First Name");

		expect(nameInput).toHaveValue("first_name");
	});

	it("auto-generates a label from the field name", async () => {
		const { user } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "user_email");

		// The label is auto-generated on type.
		expect(screen.getByLabelText(/^label \*/i)).toHaveValue("User Email");
	});
});

describe("FieldConfigDialog — Save button enablement", () => {
	it("is disabled until both name and label are present", () => {
		renderDialog();

		const addBtn = screen.getByRole("button", { name: /add field/i });
		expect(addBtn).toBeDisabled();
	});

	it("becomes enabled once name and label are both filled", async () => {
		const { user } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "email");

		// Label is auto-generated to "Email", so both fields are non-empty.
		const addBtn = screen.getByRole("button", { name: /add field/i });
		expect(addBtn).toBeEnabled();
	});
});

describe("FieldConfigDialog — save flow", () => {
	it("calls onSave with a normalized FormField payload for a simple text field", async () => {
		const { user, onSave } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "email");

		await user.click(screen.getByRole("button", { name: /add field/i }));

		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0]![0];
		expect(saved).toMatchObject({
			name: "email",
			label: "Email",
			type: "text",
			required: false,
		});
	});

	it("does not save when the visibility expression is invalid", async () => {
		const { user, onSave } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "email");
		// Typing the magic string triggers our stub's invalid path.
		await user.type(
			screen.getByLabelText(/visibility expression/i),
			"INVALID",
		);

		await user.click(screen.getByRole("button", { name: /add field/i }));

		expect(onSave).not.toHaveBeenCalled();
		expect(screen.getByText(/bad expression/i)).toBeInTheDocument();
	});
});

describe("FieldConfigDialog — conditional rendering", () => {
	it("shows the Options editor for radio fields", async () => {
		const { user } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "choice");
		// Change field type to radio via the Select.
		const typeTrigger = screen.getByLabelText(/field type/i);
		await user.click(typeTrigger);
		await user.click(await screen.findByText(/radio buttons/i));

		expect(screen.getByText(/radio options/i)).toBeInTheDocument();
	});

	it("shows file-upload config fields for type=file", async () => {
		const { user } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "upload");
		const typeTrigger = screen.getByLabelText(/field type/i);
		await user.click(typeTrigger);
		await user.click(await screen.findByText(/file upload/i));

		expect(
			screen.getByLabelText(/allowed file types/i),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/max file size/i)).toBeInTheDocument();
	});

	it("shows the Options editor for multi_select fields", async () => {
		const { user } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "tags");
		const typeTrigger = screen.getByLabelText(/field type/i);
		await user.click(typeTrigger);
		await user.click(await screen.findByText(/multi-select \(dropdown\)/i));

		expect(screen.getByText(/multi-select options/i)).toBeInTheDocument();
	});

	it("saves a multi_select field with type=multi_select", async () => {
		const { user, onSave } = renderDialog();

		await user.type(screen.getByLabelText(/field name/i), "tags");
		const typeTrigger = screen.getByLabelText(/field type/i);
		await user.click(typeTrigger);
		await user.click(await screen.findByText(/multi-select \(dropdown\)/i));

		await user.click(screen.getByRole("button", { name: /add field/i }));

		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0]![0];
		expect(saved).toMatchObject({
			name: "tags",
			type: "multi_select",
		});
	});
});

it("saves query parameter opt-in from the settings switch", async () => {
	const { user, onSave } = renderDialog();
	await user.type(screen.getByLabelText(/field name/i), "customer");
	const toggle = screen.getByRole("switch", {
		name: /allow as query parameter/i,
	});
	expect(toggle).not.toBeChecked();
	await user.click(toggle);
	await user.click(screen.getByRole("button", { name: /add field/i }));
	expect(onSave.mock.calls[0]![0]).toMatchObject({
		allow_as_query_param: true,
	});
});

it("starts each palette opening with its chosen type and a fresh field draft", async () => {
	const onSave = vi.fn();
	const view = (open: boolean, defaultType: string) => (
		<ThemeProvider>
			<FieldConfigDialog
				open={open}
				defaultType={defaultType}
				onClose={() => {}}
				onSave={onSave}
			/>
		</ThemeProvider>
	);
	const { user, rerender } = renderWithProviders(view(false, "text"));
	rerender(view(true, "email"));
	expect(
		screen.getByRole("combobox", { name: "Field Type" }),
	).toHaveTextContent("Email");
	await user.type(screen.getByLabelText(/field name/i), "email");
	await user.click(screen.getByRole("button", { name: "Add Field" }));
	expect(onSave).toHaveBeenCalledWith(
		expect.objectContaining({ name: "email", type: "email" }),
	);
	rerender(view(false, "email"));
	rerender(view(true, "email"));
	expect(screen.getByLabelText(/field name/i)).toHaveValue("");
	expect(
		screen.getByRole("combobox", { name: "Field Type" }),
	).toHaveTextContent("Email");
});

describe("FieldConfigDialog — context sheet", () => {
	it("closes only context on Escape and restores focus without losing the field", async () => {
		const { user, onClose } = renderDialog();
		await user.type(screen.getByLabelText(/field name/i), "kept_field");
		const trigger = screen.getByRole("button", {
			name: "Context",
		});
		await user.click(trigger);
		const sheet = screen.getByRole("dialog", { name: "Context & Helpers" });
		expect(
			within(sheet).getByText("Available Context"),
		).toBeInTheDocument();
		await user.keyboard("{Escape}");
		expect(
			screen.queryByRole("dialog", { name: "Context & Helpers" }),
		).not.toBeInTheDocument();
		expect(onClose).not.toHaveBeenCalled();
		expect(trigger).toHaveFocus();
		expect(screen.getByLabelText(/field name/i)).toHaveValue("kept_field");
		await user.click(trigger);
		await user.click(
			within(
				screen.getByRole("dialog", { name: "Context & Helpers" }),
			).getByRole("button", { name: "Close" }),
		);
		expect(
			screen.queryByRole("dialog", { name: "Context & Helpers" }),
		).not.toBeInTheDocument();
		expect(onClose).not.toHaveBeenCalled();
	});
});
