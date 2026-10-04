import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
	act,
	fireEvent,
	renderWithProviders,
	screen,
	waitFor,
} from "@/test-utils";

import type { PlatformJob } from "@/services/platformJobs";

const createExport = vi.fn();
const downloadExport = vi.fn();
const watchJob = vi.fn();
const stopWatching = vi.fn();

vi.mock("@/services/auditRetention", () => ({
	createAuditExport: (body: unknown) => createExport(body),
	downloadAuditExport: (jobId: string, filename: string) =>
		downloadExport(jobId, filename),
	watchAuditJob: (jobId: string, onUpdate: (job: PlatformJob) => void) => {
		watchJob(jobId, onUpdate);
		return stopWatching;
	},
}));
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [],
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: vi.fn(),
	}),
}));

import { AuditExportDialog } from "./AuditExportDialog";

function renderDialog(defaults: { action?: string } = {}) {
	return renderWithProviders(
		<AuditExportDialog
			defaultAction={defaults.action ?? ""}
			defaultStartDate=""
			defaultEndDate=""
			onClose={vi.fn()}
		/>,
	);
}

function chooseDates(start: string, end: string) {
	fireEvent.change(screen.getByLabelText("Start date"), {
		target: { value: start },
	});
	fireEvent.change(screen.getByLabelText("End date"), {
		target: { value: end },
	});
}

function finish(overrides: Partial<PlatformJob>) {
	const onUpdate = watchJob.mock.calls[0][1] as (job: PlatformJob) => void;
	act(() =>
		onUpdate({
			id: "job-1",
			job_type: "audit.query",
			payload_version: 1,
			priority: 500,
			title: "Export audit events",
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
		}),
	);
}

describe("AuditExportDialog", () => {
	beforeEach(() => {
		createExport.mockReset().mockResolvedValue({
			job_id: "job-1",
			status: "queued",
			reused: false,
			notification_id: null,
		});
		downloadExport.mockReset().mockResolvedValue(undefined);
		watchJob.mockReset();
		stopWatching.mockReset();
	});

	it("requires both dates", async () => {
		const user = userEvent.setup();
		renderDialog();

		await user.click(screen.getByRole("button", { name: "Start export" }));

		expect(screen.getByRole("alert")).toHaveTextContent(
			"Choose a start and end date.",
		);
		expect(createExport).not.toHaveBeenCalled();
	});

	it("refuses a range longer than 366 days", async () => {
		const user = userEvent.setup();
		renderDialog();

		chooseDates("2025-01-01", "2026-03-01");
		await user.click(screen.getByRole("button", { name: "Start export" }));

		expect(screen.getByRole("alert")).toHaveTextContent(
			"An export covers at most 366 days.",
		);
		for (const label of ["Start date", "End date"]) {
			const input = screen.getByLabelText(label);
			expect(input).toHaveAttribute("aria-invalid", "true");
			expect(input).toHaveAccessibleDescription(
				"An export covers at most 366 days.",
			);
		}
		expect(createExport).not.toHaveBeenCalled();
	});

	it("exports the chosen days and downloads the finished file", async () => {
		const user = userEvent.setup();
		renderDialog({ action: "auth." });
		expect(screen.getByLabelText("Action prefix (optional)")).toHaveValue(
			"auth.",
		);

		chooseDates("2026-01-01", "2026-01-31");
		await user.click(screen.getByRole("button", { name: "Start export" }));

		expect(createExport).toHaveBeenCalledWith({
			start_date: new Date("2026-01-01T00:00:00").toISOString(),
			end_date: new Date("2026-01-31T23:59:59.999").toISOString(),
			action: "auth.",
		});
		expect(
			await screen.findByText("Preparing export…"),
		).toBeInTheDocument();
		expect(watchJob).toHaveBeenCalledWith("job-1", expect.any(Function));

		finish({ status: "succeeded", result: { rows: 12 } });
		await user.click(
			await screen.findByRole("button", { name: "Download" }),
		);

		expect(downloadExport).toHaveBeenCalledWith(
			"job-1",
			"audit-export-2026-01-01-2026-01-31.jsonl.gz",
		);
		expect(stopWatching).toHaveBeenCalled();
	});

	it("shows why an export failed", async () => {
		const user = userEvent.setup();
		renderDialog();

		chooseDates("2026-01-01", "2026-01-31");
		await user.click(screen.getByRole("button", { name: "Start export" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		finish({
			status: "failed",
			error: {
				code: "export_failed",
				message: "An archived segment failed verification.",
				retryable: false,
			},
		});

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"An archived segment failed verification.",
		);
	});

	it("shows why a download was refused", async () => {
		downloadExport.mockRejectedValueOnce(
			new Error("Export expired; run it again."),
		);
		const user = userEvent.setup();
		renderDialog();

		chooseDates("2026-01-01", "2026-01-31");
		await user.click(screen.getByRole("button", { name: "Start export" }));
		await waitFor(() => expect(watchJob).toHaveBeenCalled());
		finish({ status: "succeeded", result: { rows: 12 } });
		await user.click(
			await screen.findByRole("button", { name: "Download" }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Export expired; run it again.",
		);
	});
});
