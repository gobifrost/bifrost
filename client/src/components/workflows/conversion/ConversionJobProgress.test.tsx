import { describe, expect, it } from "vitest";
import { screen, renderWithProviders } from "@/test-utils";
import type { PlatformJob } from "@/services/platformJobs";

import { ConversionJobProgress } from "./ConversionJobProgress";

function job(overrides: Partial<PlatformJob> = {}): PlatformJob {
	return {
		id: "job-1",
		job_type: "workflow.conversion",
		payload_version: 1,
		priority: 100,
		title: "Convert workflow access",
		execution_backend: "local",
		requested_by_user_id: "user-1",
		requested_by_name: "Admin",
		status: "queued",
		progress: { phase: "Queued", current: 0, total: null, percent: null },
		revision: 1,
		attempt: 1,
		max_attempts: 3,
		can_cancel: true,
		created_at: "2026-10-18T00:00:00Z",
		updated_at: "2026-10-18T00:00:00Z",
		...overrides,
	};
}

describe("ConversionJobProgress", () => {
	it("describes a queued analysis without presenting progress", () => {
		renderWithProviders(
			<ConversionJobProgress
				job={job()}
				kind="analysis"
				titleId="status"
			/>,
		);
		expect(
			screen.getByRole("heading", { name: "Analysis queued" }),
		).toBeVisible();
		expect(
			screen.getByText(
				"Your request is saved. Analysis starts when a runner is available.",
			),
		).toBeVisible();
		expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
	});

	it("shows measured running progress only when a total is known", () => {
		renderWithProviders(
			<ConversionJobProgress
				job={job({
					status: "running",
					progress: {
						phase: "Reading permissions",
						current: 3,
						total: 8,
						percent: 37.5,
					},
				})}
				kind="analysis"
				titleId="status"
			/>,
		);
		expect(screen.getByText("Reading permissions")).toBeVisible();
		expect(screen.getByText("3 of 8 completed")).toBeVisible();
		expect(screen.getByRole("progressbar")).toHaveAttribute(
			"aria-valuenow",
			"37.5",
		);
	});

	it("waits for a measured total before showing running progress", () => {
		renderWithProviders(
			<ConversionJobProgress
				job={job({
					status: "running",
					progress: {
						phase: "Preparing workflow inventory",
						current: 0,
						total: null,
						percent: 0,
					},
				})}
				kind="analysis"
				titleId="status"
			/>,
		);
		expect(screen.getByText("Preparing workflow inventory")).toBeVisible();
		expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
	});

	it("uses the public waiting reason and says when cancellation is stopping", () => {
		const { rerender } = renderWithProviders(
			<ConversionJobProgress
				job={job({
					status: "waiting",
					progress: {
						phase: "Waiting for repository access",
						current: 0,
						total: null,
						percent: null,
					},
				})}
				kind="analysis"
				titleId="status"
			/>,
		);
		expect(
			screen.getByRole("heading", { name: "Analysis is waiting" }),
		).toBeVisible();
		expect(screen.getByText("Waiting for repository access")).toBeVisible();
		rerender(
			<ConversionJobProgress
				job={job({ status: "cancel_requested" })}
				kind="apply"
				titleId="status"
			/>,
		);
		expect(
			screen.getByRole("heading", { name: "Stopping requested changes" }),
		).toBeVisible();
	});

	it("does not describe a mutating request as read-only", () => {
		renderWithProviders(
			<ConversionJobProgress job={job()} kind="apply" titleId="status" />,
		);
		expect(
			screen.getByText(
				"Your request is saved. Changes start when a runner is available.",
			),
		).toBeVisible();
		expect(screen.queryByText(/read-only/i)).not.toBeInTheDocument();
	});
});
