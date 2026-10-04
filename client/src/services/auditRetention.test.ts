import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

const onPlatformJobUpdate = vi.fn();
const unsubscribe = vi.fn();
const observePlatformJob = vi.fn();
const cancelObservation = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		onPlatformJobUpdate: (...args: unknown[]) =>
			onPlatformJobUpdate(...args),
	},
}));
vi.mock("@/services/platformJobs", () => ({
	observePlatformJob: (...args: unknown[]) => observePlatformJob(...args),
}));

import {
	auditExportDownloadPath,
	createAuditExport,
	downloadAuditExport,
	getAuditRetention,
	previewAuditExpiry,
	runAuditArchive,
	updateAuditRetention,
	watchAuditJob,
} from "./auditRetention";

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status });

const accepted = {
	job_id: "job-1",
	status: "queued",
	reused: false,
	notification_id: null,
};

describe("audit retention service", () => {
	beforeEach(() => authFetchMock.mockReset());
	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it("loads and updates the retention settings", async () => {
		authFetchMock
			.mockResolvedValueOnce(json({ settings: { hot_days: 90 } }))
			.mockResolvedValueOnce(json({ settings: { hot_days: 30 } }));

		await expect(getAuditRetention()).resolves.toEqual({
			settings: { hot_days: 90 },
		});
		await updateAuditRetention({ hot_days: 30, archive_days: null });

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/audit-retention/settings",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/audit-retention/settings",
			expect.objectContaining({
				method: "PUT",
				body: '{"hot_days":30,"archive_days":null}',
			}),
		);
	});

	it("starts an archive run or a dry run", async () => {
		authFetchMock.mockResolvedValueOnce(json(accepted, 202));

		await expect(runAuditArchive({ dry_run: true })).resolves.toEqual(
			accepted,
		);
		expect(authFetchMock).toHaveBeenCalledWith(
			"/api/maintenance/audit-retention/run",
			expect.objectContaining({
				method: "POST",
				body: '{"dry_run":true}',
			}),
		);
	});

	it("previews expiry for a window, omitting it to keep archives forever", async () => {
		const preview = {
			expiring_segments: 2,
			expiring_rows: 40,
			expiring_from: "2025-01-01",
			expiring_to: "2025-01-02",
		};
		authFetchMock
			.mockResolvedValueOnce(json(preview))
			.mockResolvedValueOnce(json(preview));

		await expect(previewAuditExpiry(180)).resolves.toEqual(preview);
		await previewAuditExpiry(null);

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/audit-retention/preview?archive_days=180",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/audit-retention/preview",
			undefined,
		);
	});

	it("queues an export and surfaces API errors", async () => {
		authFetchMock.mockResolvedValueOnce(json(accepted, 202));
		const body = {
			start_date: "2026-01-01T00:00:00.000Z",
			end_date: "2026-02-01T00:00:00.000Z",
			action: "auth.",
		};

		await expect(createAuditExport(body)).resolves.toEqual(accepted);
		expect(authFetchMock).toHaveBeenCalledWith(
			"/api/audit/exports",
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify(body),
			}),
		);

		authFetchMock.mockResolvedValueOnce(
			json({ detail: "an export covers at most 366 days" }, 422),
		);
		await expect(createAuditExport(body)).rejects.toThrow(
			"an export covers at most 366 days",
		);
	});

	it("downloads a finished export as a file", async () => {
		const click = vi.fn();
		vi.spyOn(document, "createElement").mockReturnValue({
			click,
		} as unknown as HTMLAnchorElement);
		vi.stubGlobal("URL", {
			createObjectURL: vi.fn(() => "blob:export"),
			revokeObjectURL: vi.fn(),
		});
		authFetchMock.mockResolvedValueOnce(
			new Response("gz", { status: 200 }),
		);

		await downloadAuditExport("job-1", "audit-export.jsonl.gz");

		expect(auditExportDownloadPath("job-1")).toBe(
			"/api/audit/exports/job-1/download",
		);
		expect(authFetchMock).toHaveBeenCalledWith(
			"/api/audit/exports/job-1/download",
		);
		expect(click).toHaveBeenCalledOnce();
		expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:export");
	});

	it("reports why a download was refused", async () => {
		authFetchMock.mockResolvedValueOnce(
			json({ detail: "Export expired; run it again." }, 410),
		);

		await expect(
			downloadAuditExport("job-1", "audit-export.jsonl.gz"),
		).rejects.toThrow("Export expired; run it again.");
	});

	it("watches a job over the WebSocket with one snapshot, and stops both", () => {
		onPlatformJobUpdate.mockReturnValue(unsubscribe);
		observePlatformJob.mockReturnValue({
			promise: Promise.resolve(undefined),
			cancel: cancelObservation,
		});
		const onUpdate = vi.fn();

		const stop = watchAuditJob("job-1", onUpdate);

		expect(onPlatformJobUpdate).toHaveBeenCalledWith("job-1", onUpdate);
		expect(observePlatformJob).toHaveBeenCalledWith("job-1", onUpdate);
		stop();
		expect(unsubscribe).toHaveBeenCalledOnce();
		expect(cancelObservation).toHaveBeenCalledOnce();
	});
});
