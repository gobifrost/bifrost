import { beforeEach, describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";
const mockParameters = vi.hoisted(() => vi.fn());
vi.mock("@/services/workflowParameters", () => ({
	useWorkflowParameters: mockParameters,
}));
import {
	ExecutionCancelDialog,
	ExecutionRerunDialog,
} from "./ExecutionDialogs";

describe("ExecutionCancelDialog", () => {
	it("renders the workflow name in the description", () => {
		renderWithProviders(
			<ExecutionCancelDialog
				open={true}
				onOpenChange={vi.fn()}
				workflowName="send_emails"
				onConfirm={vi.fn()}
			/>,
		);
		expect(screen.getByText("send_emails")).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: /cancel execution/i }),
		).toBeInTheDocument();
	});

	it("calls onConfirm when the confirm action is clicked", async () => {
		const onConfirm = vi.fn();
		const { user } = renderWithProviders(
			<ExecutionCancelDialog
				open={true}
				onOpenChange={vi.fn()}
				workflowName="x"
				onConfirm={onConfirm}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: /yes, cancel execution/i }),
		);
		expect(onConfirm).toHaveBeenCalledTimes(1);
	});

	it("renders nothing when open is false", () => {
		renderWithProviders(
			<ExecutionCancelDialog
				open={false}
				onOpenChange={vi.fn()}
				workflowName="x"
				onConfirm={vi.fn()}
			/>,
		);
		expect(
			screen.queryByRole("heading", { name: /cancel execution/i }),
		).not.toBeInTheDocument();
	});
});

const parameters: components["schemas"]["WorkflowParameter"][] = [
	{ name: "message", type: "str", required: true, label: "Message" },
	{
		name: "enabled",
		type: "bool",
		required: false,
		label: "Enabled",
		default_value: true,
	},
	{
		name: "count",
		type: "int",
		required: false,
		label: "Count",
		default_value: 5,
	},
	{ name: "payload", type: "dict", required: false, label: "Payload" },
	{ name: "items", type: "list", required: false, label: "Items" },
	{
		name: "mode",
		type: "str",
		required: false,
		label: "Mode",
		options: [
			{ value: "manual", label: "Manual" },
			{ value: "auto", label: "Automatic" },
		],
	},
	{
		name: "new_field",
		type: "str",
		required: false,
		label: "New Field",
		default_value: "new default",
	},
];
const inputData = {
	message: "previous",
	enabled: false,
	count: 0,
	payload: { nested: [1, true] },
	items: ["a"],
	mode: "manual",
	removed: "old",
};

function renderRerun(
	props: Partial<React.ComponentProps<typeof ExecutionRerunDialog>> = {},
) {
	const onConfirm = vi.fn();
	const onOpenChange = vi.fn();
	return {
		onConfirm,
		onOpenChange,
		...renderWithProviders(
			<ExecutionRerunDialog
				open
				onOpenChange={onOpenChange}
				workflowId="workflow-id"
				workflowName="send_emails"
				inputData={inputData}
				isRerunning={false}
				onConfirm={onConfirm}
				{...props}
			/>,
		),
	};
}

describe("ExecutionRerunDialog", () => {
	beforeEach(() => {
		mockParameters.mockReturnValue({
			data: parameters,
			isError: false,
			refetch: vi.fn(),
		});
	});

	it("prefills typed inputs, adds current defaults, and submits edited inputs without removed fields", async () => {
		const { user, onConfirm } = renderRerun();
		expect(screen.getByLabelText("Message", { exact: false })).toHaveValue(
			"previous",
		);
		expect(
			screen.getByRole("combobox", { name: "Mode" }),
		).toHaveTextContent("Manual");
		expect(
			screen.getByRole("checkbox", { name: "Enabled" }),
		).not.toBeChecked();
		expect(screen.getByRole("spinbutton", { name: "Count" })).toHaveValue(
			0,
		);
		expect(
			screen.getByRole("textbox", { name: "Payload (dict)" }),
		).toHaveValue(JSON.stringify(inputData.payload, null, 2));
		expect(
			screen.getByRole("textbox", { name: "Items (list)" }),
		).toHaveValue(JSON.stringify(inputData.items, null, 2));
		await user.clear(screen.getByRole("textbox", { name: /Message/ }));
		await user.type(
			screen.getByRole("textbox", { name: /Message/ }),
			"edited",
		);
		await user.click(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		);
		expect(onConfirm).toHaveBeenCalledWith({
			message: "edited",
			enabled: false,
			count: 0,
			payload: inputData.payload,
			items: inputData.items,
			mode: "manual",
			new_field: "new default",
		});
	});

	it("retains an explicit null instead of substituting a new default", async () => {
		const { user, onConfirm } = renderRerun({
			inputData: { ...inputData, payload: null },
		});
		await user.click(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		);
		expect(onConfirm).toHaveBeenCalledWith(
			expect.objectContaining({ payload: null }),
		);
	});

	it("validates required inputs before submission", async () => {
		const { user, onConfirm } = renderRerun();
		await user.clear(screen.getByRole("textbox", { name: /Message/ }));
		await user.click(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("cancels without starting an execution", async () => {
		const { user, onConfirm, onOpenChange } = renderRerun();
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		expect(onOpenChange).toHaveBeenCalledWith(false);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("disables fields and dismissal while starting a run", async () => {
		const { user, onOpenChange } = renderRerun({ isRerunning: true });
		expect(
			screen.getByRole("button", { name: "Rerunning…" }),
		).toBeDisabled();
		expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
		expect(screen.getByRole("textbox", { name: /Message/ })).toBeDisabled();
		await user.keyboard("{Escape}");
		expect(onOpenChange).not.toHaveBeenCalled();
	});

	it("waits for parameter loading before allowing execution", () => {
		mockParameters.mockReturnValue({ data: undefined, isError: false });
		renderRerun();
		expect(screen.getByRole("status")).toHaveTextContent(
			"Loading Parameters",
		);
		expect(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		).toBeDisabled();
	});

	it("offers parameter reload after failure without permitting a blind rerun", async () => {
		const refetch = vi.fn();
		mockParameters.mockReturnValue({
			data: undefined,
			isError: true,
			refetch,
		});
		const { user } = renderRerun();
		expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load");
		expect(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Try Again" }));
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("explains a deleted workflow and prevents submission", () => {
		renderRerun({ workflowId: null });
		expect(screen.getByRole("alert")).toHaveTextContent(
			"no longer available",
		);
		expect(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		).toBeDisabled();
	});

	it("supports workflows with no parameters", async () => {
		mockParameters.mockReturnValue({ data: [], isError: false });
		const { user, onConfirm } = renderRerun();
		expect(
			screen.getByText("This workflow has no parameters."),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		);
		expect(onConfirm).toHaveBeenCalledWith({});
	});

	it("keeps edited inputs available when execution fails", async () => {
		const { user, onConfirm } = renderRerun({
			error: "Couldn't start the workflow. Your edited input is ready to retry.",
		});
		await user.type(
			screen.getByRole("textbox", { name: /Message/ }),
			" edited",
		);
		await user.click(
			screen.getByRole("button", { name: "Rerun Workflow" }),
		);
		expect(screen.getByRole("alert")).toHaveTextContent("ready to retry");
		expect(screen.getByRole("textbox", { name: /Message/ })).toHaveValue(
			"previous edited",
		);
		expect(onConfirm).toHaveBeenCalledWith(
			expect.objectContaining({ message: "previous edited" }),
		);
	});
});
