import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { useEditorStore } from "@/stores/editorStore";
import { RunPanel } from "./RunPanel";

const appendTerminalOutput = vi.fn();
const mutateAsync = vi.fn();
let metadataOverrides: Record<string, unknown> = {};

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("@/hooks/useExecutionStream", () => ({
	useExecutionStream: vi.fn(),
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({
		data: {
			workflows: [
				{
					id: "wf-1",
					name: "Design Review",
					description: "Synthetic design review workflow",
					relative_file_path: "workflows/design_review.py",
					source_file_path: "workflows/design_review.py",
					type: "workflow",
					parameters: [
						{
							name: "review_id",
							type: "string",
							required: true,
							label: "Review Id",
						},
						{
							name: "should_fail",
							type: "bool",
							required: false,
							label: "Should Fail",
						},
					],
				},
			],
		},
		isLoading: false,
		isError: false,
		hasData: true,
		error: null,
		refetch: vi.fn(),
		...metadataOverrides,
	}),
	useExecuteWorkflow: () => ({
		mutateAsync,
	}),
}));

beforeEach(() => {
	metadataOverrides = {};
	mutateAsync.mockReset();
	appendTerminalOutput.mockReset();
	useEditorStore.setState({
		tabs: [
			{
				file: {
					name: "design_review.py",
					path: "workflows/design_review.py",
					type: "file",
					size: 1,
					modified: "2026-09-07T00:00:00Z",
					extension: ".py",
					entity_type: "workflow",
					entity_id: "wf-1",
				},
				content: "print('design review')",
				encoding: "utf-8",
				unsavedChanges: false,
				saveState: "clean",
				etag: "etag-1",
			},
			{
				file: {
					name: "notes.txt",
					path: "notes.txt",
					type: "file",
					size: 1,
					modified: "2026-09-07T00:00:00Z",
					extension: ".txt",
				},
				content: "notes",
				encoding: "utf-8",
				unsavedChanges: false,
				saveState: "clean",
				etag: "etag-2",
			},
		],
		activeTabIndex: 0,
		sidebarPanel: "run",
		layoutMode: "fullscreen",
		terminalHeight: 240,
		isOpen: true,
		isLoadingFile: false,
		diffPreview: null,
		terminalOutput: null,
		currentStreamingExecutionId: null,
		appendTerminalOutput,
	});
});

describe("RunPanel", () => {
	it("renders the workflow form immediately when metadata is already available", async () => {
		renderWithProviders(<RunPanel />);

		expect(
			await screen.findByRole("heading", { name: "Workflow" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Run Workflow" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: /review id/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("checkbox", { name: /should fail/i }),
		).toBeInTheDocument();
	});

	it("layers the multi-workflow selector above editor chrome", async () => {
		metadataOverrides = {
			data: {
				workflows: [
					{
						id: "wf-1",
						name: "Design Review",
						relative_file_path: "workflows/design_review.py",
						source_file_path: "workflows/design_review.py",
						type: "workflow",
						parameters: [],
					},
					{
						id: "wf-2",
						name: "Design Publish",
						relative_file_path: "workflows/design_review.py",
						source_file_path: "workflows/design_review.py",
						type: "workflow",
						parameters: [],
					},
				],
			},
		};
		const { user } = renderWithProviders(<RunPanel />);

		await user.click(
			await screen.findByRole("combobox", {
				name: "Select workflow",
			}),
		);

		expect(
			document.querySelector('[data-slot="select-content"]'),
		).toHaveClass("z-[200]");
	});

	it("keeps the form populated after a failed submit and allows retry", async () => {
		mutateAsync
			.mockRejectedValueOnce(new Error("Synthetic execute failure"))
			.mockResolvedValueOnce({
				logs: [
					{
						level: "INFO",
						message: "Started",
						timestamp: "2026-09-07T12:00:00Z",
						source: "workflow",
					},
				],
				status: "Success",
				variables: {},
				error: null,
			});

		const { user } = renderWithProviders(<RunPanel />);

		const reviewId = await screen.findByRole("textbox", {
			name: /review id/i,
		});
		await user.type(reviewId, "DR-001");
		await user.click(
			screen.getByRole("checkbox", { name: /should fail/i }),
		);

		await user.click(screen.getByRole("button", { name: "Run Workflow" }));
		const runError = await screen.findByRole("alert");
		expect(runError).toHaveTextContent(/could not execute workflow/i);
		expect(runError).toHaveTextContent(/synthetic execute failure/i);
		expect(
			screen.getByRole("button", { name: /retry last submission/i }),
		).toBeVisible();
		expect(reviewId).toHaveValue("DR-001");

		await user.click(
			screen.getByRole("button", { name: /retry last submission/i }),
		);
		await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
		expect(mutateAsync.mock.calls[1]![0].body).toEqual(
			mutateAsync.mock.calls[0]![0].body,
		);

		useEditorStore.setState((state) => ({
			...state,
			activeTabIndex: 1,
		}));
		await waitFor(() =>
			expect(
				screen.queryByRole("button", {
					name: /retry last submission/i,
				}),
			).not.toBeInTheDocument(),
		);
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		expect(appendTerminalOutput).toHaveBeenCalledWith(
			expect.objectContaining({
				status: "Success",
				loggerOutput: [
					expect.objectContaining({
						message: "Started",
					}),
				],
			}),
		);
	});
});

it("recovers a failed workflow metadata read without offering execution", async () => {
	const refetch = vi.fn();
	metadataOverrides = {
		data: { workflows: [] },
		hasData: false,
		isError: true,
		refetch,
	};
	const { user, rerender } = renderWithProviders(<RunPanel />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not load workflows",
	);
	expect(
		screen.queryByRole("button", { name: "Run Workflow" }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry workflows" }));
	expect(refetch).toHaveBeenCalledOnce();
	metadataOverrides = {};
	rerender(<RunPanel />);
	expect(
		await screen.findByRole("button", { name: "Run Workflow" }),
	).toBeVisible();
});

it("keeps cached run controls available after a metadata refresh fails", () => {
	metadataOverrides = { isError: true, hasData: true };
	renderWithProviders(<RunPanel />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Previously loaded workflows are still available.",
	);
	expect(screen.getByRole("button", { name: "Run Workflow" })).toBeVisible();
});

describe("RunPanel service files", () => {
	it("offers supervision instead of Run Workflow for @service files", async () => {
		metadataOverrides = {
			data: {
				workflows: [
					{
						id: "svc-1",
						name: "telegram_bridge",
						description: "Bridge Telegram messages",
						relative_file_path: "workflows/telegram_bridge.py",
						source_file_path: "workflows/telegram_bridge.py",
						type: "service",
						parameters: [],
					},
				],
			},
		};
		useEditorStore.setState((state) => ({
			tabs: state.tabs.map((tab, index) =>
				index === 0
					? {
							...tab,
							file: {
								...tab.file,
								name: "telegram_bridge.py",
								path: "workflows/telegram_bridge.py",
								entity_id: "svc-1",
							},
						}
					: tab,
			),
		}));
		renderWithProviders(<RunPanel />);
		expect(
			await screen.findByText(/supervised service/i),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Run Workflow" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Open Services" }),
		).toBeInTheDocument();
	});
});
