import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { screen, renderWithProviders } from "@/test-utils";

const useConversionJob = vi.fn();
const useOperation = vi.fn();
const analyze = vi.fn();
const apply = vi.fn();

vi.mock("@/hooks/useWorkflowConversionJob", () => ({
	useWorkflowConversionJob: (...args: unknown[]) => useConversionJob(...args),
	useWorkflowConversionOperation: () => useOperation(),
}));
vi.mock("@/services/workflowConversions", () => ({
	analyzeWorkflowConversions: (...args: unknown[]) => analyze(...args),
	applyWorkflowConversion: (...args: unknown[]) => apply(...args),
	cutOverWorkflowConversions: vi.fn(),
	getWorkflowConversionReport: vi.fn(),
}));

import { WorkflowConversion } from "./WorkflowConversion";

describe("WorkflowConversion", () => {
	beforeEach(() => {
		useConversionJob.mockReturnValue({
			job: null,
			report: null,
			error: null,
			loading: false,
			refresh: vi.fn(),
			setReport: vi.fn(),
		});
		useOperation.mockReturnValue({
			job: null,
			error: null,
			refresh: vi.fn(),
		});
		analyze.mockReset();
		apply.mockReset();
	});

	it("shows the frozen selection and starts one shared analysis", async () => {
		analyze.mockResolvedValue({ job_id: "job-synthetic" });
		const { user } = renderWithProviders(<WorkflowConversion />, {
			initialEntries: ["/workflows/convert?workflows=workflow-synthetic"],
		});

		expect(
			screen.getByText(/1 selected workflow will be frozen/),
		).toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Analyze selected workflows" }),
		);
		expect(analyze).toHaveBeenCalledWith({
			workflow_ids: ["workflow-synthetic"],
			platform_cutover: false,
		});
	});

	it("uses a bounded navigation-state selection without widening it", async () => {
		const workflowIds = Array.from(
			{ length: 1_001 },
			(_, index) => `workflow-${index}`,
		);
		analyze.mockResolvedValue({ job_id: "job-synthetic" });
		const { findByRole } = render(
			<MemoryRouter
				initialEntries={[
					{
						pathname: "/workflows/convert",
						search: "?selection=true",
						state: { workflowIds },
					},
				]}
			>
				<WorkflowConversion />
			</MemoryRouter>,
		);

		await (
			await findByRole("button", { name: "Analyze selected workflows" })
		).click();
		expect(analyze).toHaveBeenCalledWith({
			workflow_ids: workflowIds,
			platform_cutover: false,
		});
	});

	it("fails closed when a bounded selection was lost", () => {
		renderWithProviders(<WorkflowConversion />, {
			initialEntries: ["/workflows/convert?selection=true"],
		});

		expect(
			screen.getByText(/selection is no longer available/i),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Analyze selected workflows" }),
		).toBeDisabled();
	});

	it("shows current identity risk and a persisted applied identity decision", async () => {
		useConversionJob.mockReturnValue({
			job: {
				id: "job-synthetic",
				status: "succeeded",
				revision: 1,
				progress: { current: 1, total: 1 },
			},
			report: {
				job_id: "job-synthetic",
				total: 1,
				offset: 0,
				limit: 25,
				items: [
					{
						workflow_id: "workflow-synthetic",
						workflow_name: "synthetic_workflow",
						source_fingerprint: "fingerprint",
						status: "already_declared",
						current_permission_mode: "restricted",
						source_permissions: ["tickets.read"],
						observed_permissions: ["tickets.read"],
						proposed_permissions: ["tickets.read"],
						identity: {
							name: "Shared synthetic identity",
							organization_id: "org-synthetic",
							shared: true,
							broad_power_warning: true,
						},
						review: { identity_strategy: "dedicated" },
					},
				],
				summary: {
					platform_cutover: false,
					observation_window_days: 30,
					ready: 0,
					needs_review: 0,
					blocked: 0,
					stale: 0,
				},
			},
			error: null,
			loading: false,
			refresh: vi.fn(),
			setReport: vi.fn(),
		});

		const { user } = renderWithProviders(<WorkflowConversion />, {
			initialEntries: ["/workflows/convert?job=job-synthetic"],
		});

		await user.click(
			screen.getByRole("button", { name: "Review synthetic_workflow" }),
		);
		expect(screen.getByText("Shared synthetic identity")).toBeVisible();
		expect(
			screen.getByText(/Shared identity · Broad authority/),
		).toBeVisible();
		expect(
			screen.getByText(/Applied review: Dedicated identity/),
		).toBeVisible();
		await user.click(screen.getByText("Identity details"));
		expect(screen.getByText(/Organization: org-synthetic/)).toBeVisible();
	});

	it("requires explicit confirmation before exposing platform cutover", () => {
		useConversionJob.mockReturnValue({
			job: {
				id: "job-synthetic",
				status: "succeeded",
				revision: 1,
				progress: { current: 1, total: 1 },
			},
			report: {
				job_id: "job-synthetic",
				total: 0,
				offset: 0,
				limit: 25,
				items: [],
				summary: {
					platform_cutover: true,
					observation_window_days: 30,
					ready: 0,
					needs_review: 0,
					blocked: 0,
					stale: 0,
				},
			},
			error: null,
			loading: false,
			refresh: vi.fn(),
			setReport: vi.fn(),
		});
		renderWithProviders(<WorkflowConversion />, {
			initialEntries: [
				"/workflows/convert?job=job-synthetic&platform=true",
			],
		});

		expect(
			screen.getByRole("button", { name: "Review platform cutover" }),
		).toBeDisabled();
	});

	it("keeps a saved cutover operation on the platform-default step", () => {
		useOperation.mockReturnValue({
			job: {
				id: "cutover-synthetic",
				job_type: "workflow.permissions.cutover",
				status: "running",
				progress: {},
			},
			error: null,
			refresh: vi.fn(),
		});
		renderWithProviders(<WorkflowConversion />, {
			initialEntries: [
				"/workflows/convert?job=job-synthetic&operation=cutover-synthetic&platform=true",
			],
		});
		expect(
			screen.getByLabelText("Platform default, current"),
		).toHaveAttribute("aria-current", "step");
	});

	it("shows stale evidence as unavailable for Apply and offers scoped reanalysis", () => {
		useConversionJob.mockReturnValue({
			job: { id: "job-synthetic", status: "succeeded", revision: 1 },
			report: {
				job_id: "job-synthetic",
				total: 1,
				offset: 0,
				limit: 25,
				items: [
					{
						workflow_id: "workflow-synthetic",
						workflow_name: "Synthetic stale workflow",
						status: "stale",
						current_permission_mode: "full",
						error: "Source changed after review.",
					},
				],
				summary: {
					platform_cutover: false,
					observation_window_days: 30,
				},
			},
			loading: false,
			refresh: vi.fn(),
			setReport: vi.fn(),
		});
		renderWithProviders(<WorkflowConversion />, {
			initialEntries: ["/workflows/convert?job=job-synthetic"],
		});
		expect(
			screen.getByRole("checkbox", {
				name: "Select Synthetic stale workflow",
			}),
		).toBeDisabled();
		expect(screen.getByText("Source changed after review.")).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Analyze this workflow again" }),
		).toHaveAttribute(
			"href",
			"/workflows/convert?workflows=workflow-synthetic",
		);
	});

	it.each([false, true])(
		"requires final confirmation and Solution approval when needed (%s)",
		async (isSolution) => {
			apply.mockResolvedValue({ job_id: "apply-synthetic" });
			useConversionJob.mockReturnValue({
				job: { id: "job-synthetic", status: "succeeded", revision: 1 },
				report: {
					job_id: "job-synthetic",
					total: 1,
					offset: 0,
					limit: 25,
					items: [
						{
							workflow_id: "workflow-synthetic",
							workflow_name: "Ready workflow",
							solution_id: isSolution
								? "solution-synthetic"
								: null,
							source_fingerprint: "fingerprint",
							status: "ready",
							current_permission_mode: "full",
							proposed_permissions: ["tickets.read"],
						},
					],
					summary: {
						platform_cutover: false,
						observation_window_days: 30,
					},
				},
				loading: false,
				refresh: vi.fn(),
				setReport: vi.fn(),
			});

			const { user } = renderWithProviders(<WorkflowConversion />, {
				initialEntries: ["/workflows/convert?job=job-synthetic"],
			});

			await user.click(
				screen.getByRole("checkbox", { name: "Select Ready workflow" }),
			);
			expect(
				screen
					.getByRole("button", { name: /Review selected/ })
					.closest("footer"),
			).not.toBeNull();
			await user.click(
				screen.getByRole("button", { name: /Review selected/ }),
			);
			expect(
				screen.getByRole("alertdialog", {
					name: "Confirm reviewed changes",
				}),
			).toBeVisible();
			expect(apply).not.toHaveBeenCalled();
			if (isSolution) {
				expect(
					screen.getByRole("button", {
						name: "Apply reviewed changes",
					}),
				).toBeDisabled();
				await user.click(
					screen.getByRole("checkbox", {
						name: "I reviewed selected Solution declarations.",
					}),
				);
			}

			await user.click(
				screen.getByRole("button", { name: "Apply reviewed changes" }),
			);
			expect(apply).toHaveBeenCalledWith("job-synthetic", {
				selections: [
					{
						workflow_id: "workflow-synthetic",
						source_fingerprint: "fingerprint",
						identity_strategy: "dedicated",
						acknowledged_unknowns: true,
						acknowledged_broad_identity: false,
					},
				],
				acknowledged_solution_requests: isSolution,
			});
		},
	);
});
