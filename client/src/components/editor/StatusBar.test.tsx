import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useEditorStore } from "@/stores/editorStore";
import { useWorkflowsStore } from "@/stores/workflowsStore";
import { StatusBar } from "./StatusBar";

afterEach(() => {
	useEditorStore.setState(useEditorStore.getInitialState());
	useWorkflowsStore.setState(useWorkflowsStore.getInitialState());
});

describe("StatusBar metadata recovery", () => {
	it("updates the open file type when workflow metadata arrives or is removed", () => {
		const path = "workflows/design_review.py";
		useEditorStore.setState({
			tabs: [
				{
					file: {
						path,
						name: "design_review.py",
						type: "file",
						modified: "2026-09-07T00:00:00Z",
					},
					content: "",
					encoding: "utf-8",
					unsavedChanges: false,
				},
			],
			activeTabIndex: 0,
		});
		render(<StatusBar />);
		expect(
			screen.getByText("Python Script", { exact: true }),
		).toBeVisible();
		act(() =>
			useWorkflowsStore.getState().setWorkflows([
				{
					id: "workflow-review",
					name: "design_review",
					type: "workflow",
					relative_file_path: path,
					is_solution_managed: false,
					access_level: "authenticated",
					permission_mode: "full",
					category: "General",
					execution_mode: "sync",
					timeout_seconds: 1800,
					endpoint_enabled: false,
					disable_global_key: false,
					public_endpoint: false,
					is_tool: false,
					cache_ttl_seconds: 300,
					time_saved: 0,
					value: 0,
					used_by_count: 0,
					created_at: "2026-09-07T00:00:00Z",
				},
			]),
		);
		expect(screen.getByText("Workflow", { exact: true })).toBeVisible();
		expect(
			screen.queryByText("Python Script", { exact: true }),
		).not.toBeInTheDocument();
		act(() => useWorkflowsStore.getState().setWorkflows([]));
		expect(
			screen.getByText("Python Script", { exact: true }),
		).toBeVisible();
	});
});
