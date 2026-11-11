import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { renderWithProviders, screen, within } from "@/test-utils";
import type { FormField } from "@/lib/client-types";
import type { FormCanvasState, FormLayout } from "./form-layout";
vi.mock("@atlaskit/pragmatic-drag-and-drop/element/adapter", () => ({
	draggable: () => () => {},
	dropTargetForElements: () => () => {},
}));
vi.mock("@atlaskit/pragmatic-drag-and-drop/combine", () => ({
	combine: () => () => {},
}));
vi.mock("@atlaskit/pragmatic-drag-and-drop-auto-scroll/element", () => ({
	autoScrollForElements: () => () => {},
}));
const metadata = vi.hoisted(() => vi.fn());
const dialogProps = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({ data: metadata() }),
}));
vi.mock("./FieldConfigDialog", () => ({
	FieldConfigDialog: (props: {
		open: boolean;
		onClose: () => void;
		onSave: (f: FormField) => void;
		defaultType?: FormField["type"];
		field?: FormField;
		workflowInputData?: unknown;
	}) => {
		dialogProps(props);
		return props.open ? (
			<div role="dialog" aria-label="field-config">
				<button
					onClick={() =>
						props.onSave(
							props.field
								? {
										...props.field,
										name: "renamed",
										label: "Renamed",
									}
								: {
										name: "new_field",
										label: "New Field",
										type: props.defaultType ?? "text",
									},
						)
					}
				>
					Save Test Field
				</button>
				<button onClick={props.onClose}>Cancel</button>
			</div>
		) : null;
	},
}));
import { FieldsPanelDnD } from "./FieldsPanelDnD";
const first: FormField = { name: "first", label: "First", type: "text" };
const second: FormField = { name: "second", label: "Second", type: "email" };
function Harness({
	fields = [],
	layout = null,
	onChange = () => {},
	workflow,
}: {
	fields?: FormField[];
	layout?: FormLayout | null;
	onChange?: (s: FormCanvasState) => void;
	workflow?: string;
}) {
	const [state, setState] = useState<FormCanvasState>({ fields, layout });
	return (
		<FieldsPanelDnD
			fields={state.fields}
			layout={state.layout}
			onChange={(next) => {
				setState(next);
				onChange(next);
			}}
			{...(workflow ? { linkedWorkflow: workflow } : {})}
		/>
	);
}
beforeEach(() => {
	metadata.mockReturnValue({ workflows: [] });
	dialogProps.mockClear();
});
describe("unified form builder", () => {
	it("keeps structural and field components in one palette beside one canvas", () => {
		renderWithProviders(<Harness />);
		expect(screen.getByText("Component Palette")).toBeInTheDocument();
		for (const name of [
			"Row",
			"Section",
			"Text Input",
			"Email",
			"File Upload",
		])
			expect(screen.getByRole("button", { name })).toBeInTheDocument();
		expect(screen.getByText("Form Canvas")).toBeInTheDocument();
		expect(screen.queryByText("Customize Layout")).not.toBeInTheDocument();
		expect(screen.queryByText("Form Layout")).not.toBeInTheDocument();
	});
	it("adds a two-column row and saves a field directly into its chosen slot", async () => {
		const changed = vi.fn();
		const { user } = renderWithProviders(<Harness onChange={changed} />);
		await user.click(screen.getByRole("button", { name: "Row" }));
		await user.click(
			screen.getByRole("button", {
				name: "Add field to column 2 of row 1 in section 1",
			}),
		);
		await user.click(
			screen.getByRole("button", { name: "Save Test Field" }),
		);
		const state = changed.mock.lastCall![0] as FormCanvasState;
		expect(state.fields.map((f) => f.name)).toEqual(["new_field"]);
		expect(
			state.layout!.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual([null, "new_field"]);
		expect(
			screen.getByRole("button", { name: "Edit New Field" }),
		).toBeInTheDocument();
	});
	it("cancels field creation without leaving a ghost field or changing slots", async () => {
		const changed = vi.fn();
		const { user } = renderWithProviders(<Harness onChange={changed} />);
		await user.click(screen.getByRole("button", { name: "Row" }));
		changed.mockClear();
		await user.click(
			screen.getByRole("button", {
				name: "Add field to column 1 of row 1 in section 1",
			}),
		);
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		expect(changed).not.toHaveBeenCalled();
		expect(
			screen.getByRole("button", {
				name: "Add field to column 1 of row 1 in section 1",
			}),
		).toBeInTheDocument();
	});
	it("renames a field in place and preserves its column when deleting it", async () => {
		const changed = vi.fn();
		const { user } = renderWithProviders(
			<Harness
				fields={[first]}
				layout={{
					sections: [
						{
							rows: [
								{
									columns: [
										{ field: null, width: 6 },
										{ field: "first", width: 6 },
									],
								},
							],
						},
					],
				}}
				onChange={changed}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Edit First" }));
		await user.click(
			screen.getByRole("button", { name: "Save Test Field" }),
		);
		expect(
			(
				changed.mock.lastCall![0] as FormCanvasState
			).layout!.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual([null, "renamed"]);
		await user.click(
			screen.getByRole("button", { name: "Delete Renamed" }),
		);
		await user.click(screen.getByRole("button", { name: "Remove Field" }));
		expect(
			(
				changed.mock.lastCall![0] as FormCanvasState
			).layout!.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual([null, null]);
	});
	it("retains keyboard field movement and focuses the moved field", async () => {
		const { user } = renderWithProviders(
			<Harness fields={[first, second]} />,
		);
		await user.click(screen.getByRole("button", { name: "Move First" }));
		await user.click(screen.getByRole("menuitem", { name: "Move Down" }));
		expect(screen.getByRole("status")).toHaveTextContent(
			"First moved to position 2 of 2.",
		);
		expect(
			screen.getByRole("button", { name: "Move First" }),
		).toHaveFocus();
		expect(
			screen
				.getAllByRole("button", { name: /^Edit / })
				.map((b) => b.getAttribute("aria-label")),
		).toEqual(["Edit Second", "Edit First"]);
	});
	it("moves an existing field into an empty column through the keyboard menu", async () => {
		const changed = vi.fn();
		const { user } = renderWithProviders(
			<Harness
				fields={[first]}
				layout={{
					sections: [
						{
							rows: [
								{
									columns: [
										{ field: "first", width: 6 },
										{ field: null, width: 6 },
									],
								},
							],
						},
					],
				}}
				onChange={changed}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Move First" }));
		await user.keyboard("{End}{ArrowRight}{Enter}");
		expect(
			(
				changed.mock.lastCall![0] as FormCanvasState
			).layout!.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual([null, "first"]);
		expect(
			screen.getByRole("button", { name: "Move First" }),
		).toHaveFocus();
	});

	it("keeps workflow input prefill and excludes inputs already on the canvas", async () => {
		metadata.mockReturnValue({
			workflows: [
				{
					id: "wf",
					parameters: [
						{ name: "first", type: "str" },
						{
							name: "email_param",
							type: "str",
							required: true,
							dataProvider: "provider-id",
							description: "Choose user",
						},
					],
				},
			],
		});
		const { user } = renderWithProviders(
			<Harness fields={[first]} workflow="wf" />,
		);
		const palette =
			screen.getByText("Workflow Inputs").parentElement!.parentElement!;
		expect(within(palette).queryByText("first")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /email_param/ }));
		expect(dialogProps.mock.lastCall![0].workflowInputData).toEqual({
			name: "email_param",
			required: true,
			helpText: "Choose user",
			dataProvider: "provider-id",
		});
	});
	it("offers keyboard palette selection and rejects presets that would lose fields", async () => {
		const { user } = renderWithProviders(
			<Harness
				fields={[first, second]}
				layout={{
					sections: [
						{
							rows: [
								{
									columns: [
										{ field: "first", width: 6 },
										{ field: "second", width: 6 },
									],
								},
							],
						},
					],
				}}
			/>,
		);
		const columns = screen.getByRole("combobox", {
			name: "Columns for row 1 in section 1",
		});
		expect(
			within(columns).getByRole("option", {
				name: "1 Column",
			}),
		).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Choose a Component" }),
		);
		screen.getByRole("button", { name: "Text Input" }).focus();
		await user.keyboard("{Enter}");
		expect(
			screen.getByRole("dialog", { name: "field-config" }),
		).toBeInTheDocument();
	});
});
