import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import { WorkflowParametersForm } from "./WorkflowParametersForm";

vi.mock("@/components/tables/CodeEditor", () => ({
	CodeEditor: ({
		text,
		onChange,
		ariaLabel,
		readOnly,
		path,
	}: {
		text: string;
		onChange: (value: string) => void;
		ariaLabel: string;
		readOnly: boolean;
		path: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={text}
			disabled={readOnly}
			data-model-path={path}
			onChange={(event) => onChange(event.target.value)}
		/>
	),
}));
const parameters = [
	{
		name: "payload",
		label: "Payload",
		type: "dict",
		required: false,
		default_value: { count: 1 },
	},
	{
		name: "items",
		label: "Items",
		type: "list",
		required: false,
		default_value: [1, 2],
	},
	{
		name: "document",
		label: "Document",
		type: "json",
		required: false,
		default_value: { enabled: false },
	},
];

describe("WorkflowParametersForm structured inputs", () => {
	it("uses JSON editors for objects, arrays, and JSON and submits parsed values", async () => {
		const onExecute = vi.fn();
		const { user } = renderWithProviders(
			<WorkflowParametersForm
				parameters={parameters}
				onExecute={onExecute}
			/>,
		);
		expect(
			screen.getByRole("textbox", { name: "Payload (dict)" }),
		).toHaveValue('{\n  "count": 1\n}');
		expect(
			screen.getByRole("textbox", { name: "Items (list)" }),
		).toHaveValue("[\n  1,\n  2\n]");
		fireEvent.change(
			screen.getByRole("textbox", { name: "Payload (dict)" }),
			{ target: { value: '{ "count": 2 }' } },
		);
		fireEvent.change(
			screen.getByRole("textbox", { name: "Items (list)" }),
			{ target: { value: "[3, 4]" } },
		);
		await user.click(
			screen.getByRole("button", { name: "Execute Workflow" }),
		);
		expect(onExecute).toHaveBeenCalledWith({
			payload: { count: 2 },
			items: [3, 4],
			document: { enabled: false },
		});
	});

	it("preserves formatting and incomplete JSON across edits to other fields", () => {
		renderWithProviders(
			<WorkflowParametersForm
				parameters={parameters}
				onExecute={vi.fn()}
			/>,
		);
		const payload = screen.getByRole("textbox", { name: "Payload (dict)" });
		fireEvent.change(payload, { target: { value: '{ "count": 2 }' } });
		expect(payload).toHaveValue('{ "count": 2 }');
		fireEvent.change(payload, { target: { value: '{ "count":' } });
		fireEvent.change(
			screen.getByRole("textbox", { name: "Items (list)" }),
			{ target: { value: "[3]" } },
		);
		expect(payload).toHaveValue('{ "count":');
	});

	it("accepts externally replaced values in controlled forms", () => {
		const { rerender } = renderWithProviders(
			<WorkflowParametersForm
				parameters={parameters}
				values={{ payload: { count: 2 } }}
				onChange={vi.fn()}
				onExecute={vi.fn()}
			/>,
		);
		rerender(
			<WorkflowParametersForm
				parameters={parameters}
				values={{ payload: { count: 9 } }}
				onChange={vi.fn()}
				onExecute={vi.fn()}
			/>,
		);
		expect(
			screen.getByRole("textbox", { name: "Payload (dict)" }),
		).toHaveValue('{\n  "count": 9\n}');
	});

	it("keeps editor models separate between fields and forms", () => {
		renderWithProviders(
			<>
				<WorkflowParametersForm
					parameters={parameters}
					onExecute={vi.fn()}
				/>
				<WorkflowParametersForm
					parameters={parameters}
					onExecute={vi.fn()}
				/>
			</>,
		);
		const paths = screen
			.getAllByRole("textbox")
			.map((element) => element.getAttribute("data-model-path"));
		expect(new Set(paths).size).toBe(6);
	});

	it("locks structured inputs while execution is pending", () => {
		renderWithProviders(
			<WorkflowParametersForm
				parameters={parameters}
				isExecuting
				onExecute={vi.fn()}
			/>,
		);
		for (const editor of screen.getAllByRole("textbox"))
			expect(editor).toBeDisabled();
	});
});
