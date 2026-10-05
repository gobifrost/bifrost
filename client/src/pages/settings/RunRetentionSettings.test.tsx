import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
import type { PlatformJob } from "@/services/platformJobs";
import type {
	RunRetentionSettingsUpdate,
	RunRetentionStatus,
} from "@/services/runRetention";

const getRetention = vi.fn();
const updateRetention = vi.fn();
const previewRetention = vi.fn();
const startRetention = vi.fn();
const watchJob = vi.fn();
const stopWatching = vi.fn();

vi.mock("@/services/runRetention", () => ({
	RUN_RETENTION_DAYS_QUERY_KEY: ["run-retention"],
	getRunRetention: () => getRetention(),
	updateRunRetention: (body: unknown) => updateRetention(body),
	previewRunRetention: (days: unknown) => previewRetention(days),
	startRunRetention: (dryRun: unknown) => startRetention(dryRun),
}));
vi.mock("@/services/auditRetention", () => ({
	watchAuditJob: (jobId: string, onUpdate: (job: PlatformJob) => void) => {
		watchJob(jobId, onUpdate);
		return stopWatching;
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ApiError } from "@/lib/api-error";
import { formatRelativeTime } from "@/lib/utils";
import {
	formatAuditDay,
	formatAuditTime,
} from "@/pages/audit/auditRetentionFormat";
import { RunRetentionSettings } from "./RunRetentionSettings";

function job(overrides: Partial<PlatformJob> = {}): PlatformJob {
	return {
		id: "job-1",
		job_type: "run.retention",
		payload_version: 1,
		priority: 500,
		title: "Delete expired runs and events",
		execution_backend: "local",
		requested_by_user_id: "user-1",
		requested_by_name: "Admin",
		status: "running",
		progress: { phase: null, current: 0, total: null, percent: null },
		revision: 1,
		attempt: 1,
		max_attempts: 3,
		can_cancel: false,
		created_at: "2026-10-05T02:59:00Z",
		updated_at: "2026-10-05T03:00:00Z",
		...overrides,
	};
}

const LAST_RUN_AT = "2026-10-05T03:00:00Z";

function status(
	days: number | null = 60,
	last_run: PlatformJob | null = job({
		status: "succeeded",
		completed_at: LAST_RUN_AT,
		result: {
			dry_run: false,
			cutoff: "2026-08-06T03:00:00Z",
			workflow_runs_deleted: 12,
			agent_runs_deleted: 1,
			events_deleted: 400,
			continues: false,
		},
	}),
): RunRetentionStatus {
	return {
		settings: { days },
		info: {
			days,
			oldest_finished_run: "2026-08-07T09:30:00Z",
			rolled_up_runs: 5200,
			rolled_up_through: "2026-08-06",
		},
		last_run,
	};
}

async function renderLoaded() {
	renderWithProviders(<RunRetentionSettings />);
	const days = await screen.findByLabelText(
		"Keep finished runs and events (days)",
	);
	await waitFor(() => expect(days).toBeEnabled());
	return days;
}

function completeJob(overrides: Partial<PlatformJob>) {
	const onUpdate = watchJob.mock.calls[0][1] as (job: PlatformJob) => void;
	act(() => onUpdate(job(overrides)));
}

describe("RunRetentionSettings", () => {
	beforeEach(() => {
		getRetention.mockReset().mockResolvedValue(status());
		updateRetention
			.mockReset()
			.mockImplementation((body: RunRetentionSettingsUpdate) =>
				Promise.resolve(status(body.days)),
			);
		previewRetention.mockReset().mockResolvedValue({
			days: 30,
			cutoff: "2026-09-05T03:00:00Z",
			workflow_runs: 12345,
			agent_runs: 210,
			events: 4000,
		});
		startRetention.mockReset().mockResolvedValue({
			job_id: "job-1",
			status: "queued",
			reused: false,
			notification_id: null,
		});
		watchJob.mockReset();
		stopWatching.mockReset();
	});

	it("loads the window, what is kept, and the last run", async () => {
		const days = await renderLoaded();

		expect(screen.getByText("Run history")).toBeInTheDocument();
		expect(days).toHaveValue(60);
		expect(
			screen.getByRole("switch", { name: "Keep forever" }),
		).not.toBeChecked();
		expect(
			screen.getByText(
				`Oldest kept run: ${formatAuditTime("2026-08-07T09:30:00Z")}. Rolled-up history: 5,200 runs through ${formatAuditDay("2026-08-06")}.`,
			),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				`Last run ${formatRelativeTime(LAST_RUN_AT)}: deleted 12 workflow runs, 1 agent run, 400 events.`,
			),
		).toBeInTheDocument();
	});

	it("says when nothing has run yet or nothing is kept", async () => {
		getRetention.mockResolvedValue({
			...status(60, null),
			info: {
				days: 60,
				oldest_finished_run: null,
				rolled_up_runs: 0,
				rolled_up_through: null,
			},
		});
		await renderLoaded();

		expect(
			screen.getByText(
				"No finished runs are kept. No rolled-up history yet.",
			),
		).toBeInTheDocument();
		expect(screen.getByText("Not run yet.")).toBeInTheDocument();
	});

	it("keeps runs forever without confirmation and disables the field", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		const days = await screen.findByLabelText(
			"Keep finished runs and events (days)",
		);
		await waitFor(() => expect(days).toBeEnabled());

		await user.click(screen.getByRole("switch", { name: "Keep forever" }));

		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({ days: null }),
		);
		expect(days).toBeDisabled();
		expect(previewRetention).not.toHaveBeenCalled();
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
	});

	it("confirms the exact deletion before shortening, then saves", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		const days = await screen.findByLabelText(
			"Keep finished runs and events (days)",
		);
		await waitFor(() => expect(days).toBeEnabled());

		fireEvent.change(days, { target: { value: "30" } });
		fireEvent.blur(days);

		const dialog = await screen.findByRole("alertdialog");
		expect(previewRetention).toHaveBeenCalledWith(30);
		expect(dialog).toHaveTextContent(
			"Deletes 12,345 workflow runs, 210 agent runs and 4,000 events at the next daily run.",
		);
		expect(updateRetention).not.toHaveBeenCalled();

		await user.click(
			screen.getByRole("button", { name: "Delete and save" }),
		);
		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({ days: 30 }),
		);
		await waitFor(() => expect(days).toHaveFocus());
	});

	it("restores the saved window when shortening is cancelled", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		const days = await screen.findByLabelText(
			"Keep finished runs and events (days)",
		);
		await waitFor(() => expect(days).toBeEnabled());

		fireEvent.change(days, { target: { value: "30" } });
		fireEvent.blur(days);
		await screen.findByRole("alertdialog");
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		await waitFor(() => expect(days).toHaveFocus());
		expect(days).toHaveValue(60);
		expect(updateRetention).not.toHaveBeenCalled();
	});

	it("confirms before turning keep-forever off, previewing the typed window", async () => {
		getRetention.mockResolvedValue(status(null));
		const { user } = renderWithProviders(<RunRetentionSettings />);
		const forever = await screen.findByRole("switch", {
			name: "Keep forever",
		});
		await waitFor(() => expect(forever).toBeChecked());

		await user.click(forever);

		await screen.findByRole("alertdialog");
		expect(previewRetention).toHaveBeenCalledWith(30);
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		await waitFor(() => expect(forever).toBeChecked());
		expect(updateRetention).not.toHaveBeenCalled();
	});

	it.each([
		{
			counts: { workflow_runs: 1, agent_runs: 1, events: 1 },
			text: "Deletes 1 workflow run, 1 agent run and 1 event at the next daily run.",
		},
		{
			counts: { workflow_runs: 0, agent_runs: 0, events: 0 },
			text: "Nothing is old enough to delete yet.",
		},
	])("words a preview of $counts", async ({ counts, text }) => {
		previewRetention.mockResolvedValue({
			days: 30,
			cutoff: "2026-09-05T03:00:00Z",
			...counts,
		});
		const days = await renderLoaded();

		fireEvent.change(days, { target: { value: "30" } });
		fireEvent.blur(days);

		expect(await screen.findByRole("alertdialog")).toHaveTextContent(text);
	});

	it("raises the window without confirmation", async () => {
		const days = await renderLoaded();

		fireEvent.change(days, { target: { value: "90" } });
		fireEvent.blur(days);

		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({ days: 90 }),
		);
		expect(previewRetention).not.toHaveBeenCalled();
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
	});

	it("refuses a window under the minimum before asking the server", async () => {
		const days = await renderLoaded();

		fireEvent.change(days, { target: { value: "7" } });
		fireEvent.blur(days);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Keep finished runs and events for 30–3,650 days.",
		);
		expect(days).toHaveAttribute("aria-invalid", "true");
		expect(previewRetention).not.toHaveBeenCalled();
		expect(updateRetention).not.toHaveBeenCalled();
	});

	it("shows why the server refused a save, without offering a retry", async () => {
		updateRetention.mockRejectedValueOnce(
			new ApiError("days must be at most 3650", 422),
		);
		const days = await renderLoaded();

		fireEvent.change(days, { target: { value: "90" } });
		fireEvent.blur(days);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Couldn't save run history settings: days must be at most 3650",
		);
		expect(
			screen.queryByRole("button", { name: "Retry save" }),
		).not.toBeInTheDocument();
	});

	it("offers a retry when the save could not reach the server", async () => {
		updateRetention.mockRejectedValueOnce(new TypeError("Failed to fetch"));
		const { user } = renderWithProviders(<RunRetentionSettings />);
		const days = await screen.findByLabelText(
			"Keep finished runs and events (days)",
		);
		await waitFor(() => expect(days).toBeEnabled());

		fireEvent.change(days, { target: { value: "90" } });
		fireEvent.blur(days);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Couldn't save run history settings: Failed to fetch",
		);
		await user.click(screen.getByRole("button", { name: "Retry save" }));
		await waitFor(() => expect(updateRetention).toHaveBeenCalledTimes(2));
	});

	it("previews a run from the job's result, counts in the status line only", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		await screen.findByLabelText("Keep finished runs and events (days)");
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Preview" }),
			).toBeEnabled(),
		);

		await user.click(screen.getByRole("button", { name: "Preview" }));

		expect(startRetention).toHaveBeenCalledWith(true);
		await waitFor(() =>
			expect(watchJob).toHaveBeenCalledWith(
				"job-1",
				expect.any(Function),
			),
		);
		const preview = {
			status: "succeeded" as const,
			completed_at: LAST_RUN_AT,
			result: {
				dry_run: true,
				cutoff: "2026-08-06T03:00:00Z",
				workflow_runs: 12345,
				agent_runs: 1,
				events: 4000,
			},
		};
		getRetention.mockResolvedValue(status(60, job(preview)));
		completeJob(preview);

		expect(await screen.findByRole("status")).toHaveTextContent(
			/^Would delete 12,345 workflow runs, 1 agent run and 4,000 events\.$/,
		);
		expect(
			await screen.findByText(
				`Last preview ${formatRelativeTime(LAST_RUN_AT)}.`,
			),
		).toBeInTheDocument();
		expect(screen.getAllByText(/Would delete/)).toHaveLength(1);
		expect(stopWatching).toHaveBeenCalled();
	});

	it("says when a preview finds nothing to delete", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		await screen.findByLabelText("Keep finished runs and events (days)");
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Preview" }),
			).toBeEnabled(),
		);

		await user.click(screen.getByRole("button", { name: "Preview" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		completeJob({
			status: "succeeded",
			result: {
				dry_run: true,
				workflow_runs: 0,
				agent_runs: 0,
				events: 0,
			},
		});

		expect(
			await screen.findByText("Nothing to delete."),
		).toBeInTheDocument();
	});

	it("says when a preview ran while runs are kept forever", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		await screen.findByLabelText("Keep finished runs and events (days)");
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Preview" }),
			).toBeEnabled(),
		);

		await user.click(screen.getByRole("button", { name: "Preview" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		completeJob({
			status: "succeeded",
			result: { dry_run: true, skipped: "keep_forever" },
		});

		expect(
			await screen.findByText(
				"Runs are kept forever; nothing would be deleted.",
			),
		).toBeInTheDocument();
	});

	it("starts a real run and refreshes the last run, noting when it continues", async () => {
		const { user } = renderWithProviders(<RunRetentionSettings />);
		await screen.findByLabelText("Keep finished runs and events (days)");
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Run now" }),
			).toBeEnabled(),
		);

		await user.click(screen.getByRole("button", { name: "Run now" }));

		expect(startRetention).toHaveBeenCalledWith(false);
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		getRetention.mockResolvedValue(
			status(
				60,
				job({
					status: "succeeded",
					completed_at: LAST_RUN_AT,
					result: {
						dry_run: false,
						cutoff: "2026-08-06T03:00:00Z",
						workflow_runs_deleted: 100000,
						agent_runs_deleted: 2,
						events_deleted: 100000,
						continues: true,
					},
				}),
			),
		);
		completeJob({ status: "succeeded" });

		expect(
			await screen.findByText(
				`Last run ${formatRelativeTime(LAST_RUN_AT)}: deleted 100,000 workflow runs, 2 agent runs, 100,000 events. Continues tomorrow.`,
			),
		).toBeInTheDocument();
	});

	it("shows a skipped run and why a run failed", async () => {
		getRetention.mockResolvedValue(
			status(
				null,
				job({
					status: "succeeded",
					completed_at: LAST_RUN_AT,
					result: { dry_run: false, skipped: "keep_forever" },
				}),
			),
		);
		const { user } = renderWithProviders(<RunRetentionSettings />);
		expect(
			await screen.findByText(
				`Last run ${formatRelativeTime(LAST_RUN_AT)}: skipped, runs are kept forever.`,
			),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Run now" }),
			).toBeEnabled(),
		);

		await user.click(screen.getByRole("button", { name: "Run now" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		completeJob({
			status: "failed",
			error: {
				code: "boom",
				message: "The lease was lost.",
				retryable: true,
			},
		});

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"The lease was lost.",
		);
	});
});
