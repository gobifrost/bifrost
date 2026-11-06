import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
	useWorkflowConversionJob,
	useWorkflowConversionOperation,
} from "./useWorkflowConversionJob";

const getPlatformJob = vi.fn();
const getWorkflowConversionReport = vi.fn();
type JobUpdate = {
	id: string;
	revision: number;
	status: string;
	progress: { current: number; total: number };
};

let jobListener: ((job: JobUpdate) => void) | undefined;
let connectionListener: ((connected: boolean) => void) | undefined;

vi.mock("@/services/platformJobs", () => ({
	getPlatformJob: (...args: unknown[]) => getPlatformJob(...args),
}));
vi.mock("@/services/workflowConversions", () => ({
	getWorkflowConversionReport: (...args: unknown[]) =>
		getWorkflowConversionReport(...args),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		onAnyPlatformJobUpdate: (listener: (job: JobUpdate) => void) => {
			jobListener = listener;
			return () => {
				jobListener = undefined;
			};
		},
		onConnectionStatusChange: (listener: (connected: boolean) => void) => {
			connectionListener = listener;
			return () => {
				connectionListener = undefined;
			};
		},
		onPlatformJobUpdate: (
			_id: string,
			_listener: (job: JobUpdate) => void,
		) => {
			return () => undefined;
		},
	},
}));

function job(id: string, revision: number): JobUpdate {
	return {
		id,
		revision,
		status: "running",
		progress: { current: 1, total: 2 },
	};
}

describe("useWorkflowConversionJob", () => {
	beforeEach(() => {
		getPlatformJob.mockReset();
		getWorkflowConversionReport.mockReset();
		jobListener = undefined;
		connectionListener = undefined;
	});

	it("does not let a stale response replace a newly opened job", async () => {
		let resolveOld!: (value: JobUpdate) => void;
		getPlatformJob
			.mockImplementationOnce(
				() =>
					new Promise((resolve) => {
						resolveOld = resolve;
					}),
			)
			.mockResolvedValueOnce(job("job-new", 1));
		getWorkflowConversionReport.mockResolvedValue({
			job_id: "job-new",
			items: [],
		});
		const { result, rerender } = renderHook(
			({ id }) => useWorkflowConversionJob(id),
			{ initialProps: { id: "job-old" } },
		);
		await waitFor(() =>
			expect(getPlatformJob).toHaveBeenCalledWith("job-old"),
		);
		rerender({ id: "job-new" });
		await waitFor(() => expect(result.current.job?.id).toBe("job-new"));
		await act(async () => {
			resolveOld(job("job-old", 9));
		});
		expect(result.current.job?.id).toBe("job-new");
	});

	it("accepts newer websocket updates and refreshes once on reconnect", async () => {
		getPlatformJob.mockResolvedValue(job("job-synthetic", 3));
		getWorkflowConversionReport.mockResolvedValue({
			job_id: "job-synthetic",
			items: [],
		});
		const { result } = renderHook(() =>
			useWorkflowConversionJob("job-synthetic"),
		);
		await waitFor(() => expect(result.current.job?.revision).toBe(3));
		act(() => jobListener?.(job("job-synthetic", 2)));
		expect(result.current.job?.revision).toBe(3);
		act(() => jobListener?.(job("job-synthetic", 4)));
		expect(result.current.job?.revision).toBe(4);
		act(() => connectionListener?.(true));
		await waitFor(() => expect(getPlatformJob).toHaveBeenCalledTimes(2));
	});
});

describe("useWorkflowConversionOperation", () => {
	beforeEach(() => {
		getPlatformJob.mockReset();
		connectionListener = undefined;
	});

	it("does not let an old operation response replace a newer operation", async () => {
		let resolveOld!: (value: JobUpdate) => void;
		getPlatformJob
			.mockImplementationOnce(
				() =>
					new Promise((resolve) => {
						resolveOld = resolve;
					}),
			)
			.mockResolvedValueOnce(job("operation-new", 1));
		const { result, rerender } = renderHook(
			({ id }) => useWorkflowConversionOperation(id),
			{ initialProps: { id: "operation-old" } },
		);
		await waitFor(() =>
			expect(getPlatformJob).toHaveBeenCalledWith("operation-old"),
		);
		rerender({ id: "operation-new" });
		await waitFor(() =>
			expect(result.current.job?.id).toBe("operation-new"),
		);
		await act(async () => {
			resolveOld(job("operation-old", 9));
		});
		expect(result.current.job?.id).toBe("operation-new");
	});
});
