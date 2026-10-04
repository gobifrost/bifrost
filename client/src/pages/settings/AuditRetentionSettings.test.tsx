import {
	act,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
	AuditRetentionSettingsUpdate,
	AuditRetentionStatus,
} from "@/services/auditRetention";
import type { PlatformJob } from "@/services/platformJobs";

const getRetention = vi.fn();
const updateRetention = vi.fn();
const runArchive = vi.fn();
const previewExpiry = vi.fn();
const watchJob = vi.fn();
const stopWatching = vi.fn();

vi.mock("@/services/auditRetention", () => ({
	getAuditRetention: () => getRetention(),
	updateAuditRetention: (body: unknown) => updateRetention(body),
	runAuditArchive: (body: unknown) => runArchive(body),
	previewAuditExpiry: (days: unknown) => previewExpiry(days),
	watchAuditJob: (jobId: string, onUpdate: (job: PlatformJob) => void) => {
		watchJob(jobId, onUpdate);
		return stopWatching;
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AuditRetentionSettings } from "./AuditRetentionSettings";
import {
	formatAuditDay,
	formatAuditTime,
} from "@/pages/audit/auditRetentionFormat";

function job(overrides: Partial<PlatformJob> = {}): PlatformJob {
	return {
		id: "job-1",
		job_type: "audit.archive",
		payload_version: 1,
		priority: 500,
		title: "Archive audit events",
		execution_backend: "local",
		requested_by_user_id: "user-1",
		requested_by_name: "Admin",
		status: "running",
		progress: { phase: null, current: 0, total: null, percent: null },
		revision: 1,
		attempt: 1,
		max_attempts: 3,
		can_cancel: false,
		created_at: "2026-10-03T01:59:00Z",
		updated_at: "2026-10-03T02:00:00Z",
		...overrides,
	};
}

function status(
	settings: AuditRetentionSettingsUpdate = {
		hot_days: 90,
		archive_days: 365,
	},
): AuditRetentionStatus {
	return {
		settings,
		info: {
			...settings,
			oldest_in_database: "2026-07-06T09:30:00Z",
			archived_through: "2026-07-05T23:59:00Z",
			archived_segments: 12,
			archived_rows: 4200,
		},
		last_run: job({
			status: "succeeded",
			completed_at: "2026-10-03T02:00:00Z",
			result: { dry_run: false, archived_rows: 310 },
		}),
	};
}

async function renderLoaded() {
	render(<AuditRetentionSettings />);
	const database = await screen.findByLabelText("Keep in database (days)");
	await waitFor(() => expect(database).toBeEnabled());
	return {
		database,
		archive: screen.getByLabelText("Keep in archive (days)"),
	};
}

describe("AuditRetentionSettings", () => {
	beforeEach(() => {
		getRetention.mockReset().mockResolvedValue(status());
		updateRetention
			.mockReset()
			.mockImplementation((body: AuditRetentionSettingsUpdate) =>
				Promise.resolve(status(body)),
			);
		runArchive.mockReset().mockResolvedValue({
			job_id: "job-1",
			status: "queued",
			reused: false,
			notification_id: null,
		});
		previewExpiry.mockReset().mockResolvedValue({
			expiring_segments: 2,
			expiring_rows: 40,
			expiring_from: "2025-01-01",
			expiring_to: "2025-01-02",
		});
		watchJob.mockReset();
		stopWatching.mockReset();
	});

	it("loads the windows, what is stored, and the last run", async () => {
		const { database, archive } = await renderLoaded();

		expect(database).toHaveValue(90);
		expect(archive).toHaveValue(365);
		expect(
			screen.getByText(
				`Database holds events since ${formatAuditTime("2026-07-06T09:30:00Z")}. Archive holds 4,200 events through ${formatAuditTime("2026-07-05T23:59:00Z")}.`,
			),
		).toBeInTheDocument();
		expect(screen.getByText(/succeeded/)).toHaveTextContent(
			"archived 310 events",
		);
	});

	it("saves a database window without confirmation", async () => {
		const { database } = await renderLoaded();

		fireEvent.change(database, { target: { value: "30" } });
		fireEvent.blur(database);

		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({
				hot_days: 30,
				archive_days: 365,
			}),
		);
		expect(previewExpiry).not.toHaveBeenCalled();
	});

	it("refuses an archive window shorter than the database window", async () => {
		const { archive } = await renderLoaded();

		fireEvent.change(archive, { target: { value: "30" } });
		fireEvent.blur(archive);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"at least as long as events stay in the database (90 days)",
		);
		expect(archive).toHaveAttribute("aria-invalid", "true");
		expect(previewExpiry).not.toHaveBeenCalled();
		expect(updateRetention).not.toHaveBeenCalled();
	});

	it("keeps archives forever without confirmation and disables the archive field", async () => {
		const user = userEvent.setup();
		const { archive } = await renderLoaded();

		await user.click(
			screen.getByRole("switch", { name: "Keep archives forever" }),
		);

		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({
				hot_days: 90,
				archive_days: null,
			}),
		);
		expect(archive).toBeDisabled();
		expect(previewExpiry).not.toHaveBeenCalled();
	});

	it("confirms the exact deletion before shortening the archive window", async () => {
		const user = userEvent.setup();
		const { archive } = await renderLoaded();

		fireEvent.change(archive, { target: { value: "180" } });
		fireEvent.blur(archive);

		const dialog = await screen.findByRole("alertdialog");
		expect(previewExpiry).toHaveBeenCalledWith(180);
		expect(dialog).toHaveTextContent(
			`This permanently deletes 40 archived events (${formatAuditDay("2025-01-01")}–${formatAuditDay("2025-01-02")}) at the next daily run.`,
		);
		expect(updateRetention).not.toHaveBeenCalled();

		await user.click(
			screen.getByRole("button", { name: "Delete and save" }),
		);
		await waitFor(() =>
			expect(updateRetention).toHaveBeenCalledWith({
				hot_days: 90,
				archive_days: 180,
			}),
		);
	});

	it("restores the saved archive window when shortening is cancelled", async () => {
		const user = userEvent.setup();
		getRetention.mockResolvedValue(
			status({ hot_days: 90, archive_days: null }),
		);
		const { archive } = await renderLoaded();
		const forever = screen.getByRole("switch", {
			name: "Keep archives forever",
		});
		expect(archive).toBeDisabled();

		await user.click(forever);
		await screen.findByRole("alertdialog");
		expect(previewExpiry).toHaveBeenCalledWith(365);
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		await waitFor(() => expect(forever).toBeChecked());
		expect(archive).toBeDisabled();
		expect(updateRetention).not.toHaveBeenCalled();
	});

	it("previews a run from the job's result", async () => {
		const user = userEvent.setup();
		await renderLoaded();

		await user.click(screen.getByRole("button", { name: "Preview" }));

		expect(runArchive).toHaveBeenCalledWith({ dry_run: true });
		await waitFor(() =>
			expect(watchJob).toHaveBeenCalledWith(
				"job-1",
				expect.any(Function),
			),
		);
		const onUpdate = watchJob.mock.calls[0][1] as (
			job: PlatformJob,
		) => void;
		act(() =>
			onUpdate(
				job({
					status: "succeeded",
					result: {
						dry_run: true,
						eligible_rows: 1200,
						days: [
							{ day: "2026-06-01", rows: 700, bytes: 1 },
							{ day: "2026-07-05", rows: 500, bytes: 1 },
						],
						expiring_rows: 40,
					},
				}),
			),
		);

		expect(
			await screen.findByText(
				`Would archive 1,200 events from ${formatAuditDay("2026-06-01")}–${formatAuditDay("2026-07-05")}; would delete 40 archived events.`,
			),
		).toBeInTheDocument();
		expect(stopWatching).toHaveBeenCalled();
	});

	it("shows why a preview failed", async () => {
		const user = userEvent.setup();
		await renderLoaded();

		await user.click(screen.getByRole("button", { name: "Preview" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		const onUpdate = watchJob.mock.calls[0][1] as (
			job: PlatformJob,
		) => void;
		act(() =>
			onUpdate(
				job({
					status: "failed",
					error: {
						code: "boom",
						message: "Archive storage is unreachable.",
						retryable: true,
					},
				}),
			),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Archive storage is unreachable.",
		);
	});
});
