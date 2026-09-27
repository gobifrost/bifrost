import type { ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PlatformJobUpdate } from "@/services/websocket";

type JobCallback = (job: PlatformJobUpdate) => void;

const mocks = vi.hoisted(() => ({
	callback: undefined as JobCallback | undefined,
	unsubscribe: vi.fn(),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		onAnyPlatformJobUpdate: vi.fn((callback: JobCallback) => {
			mocks.callback = callback;
			return mocks.unsubscribe;
		}),
	},
}));

import { webSocketService } from "@/services/websocket";
import { useApplicationSdkUpdateJobs } from "./useApplicationSdkUpdateJobs";

function makeJob(
	overrides: Partial<PlatformJobUpdate> = {},
): PlatformJobUpdate {
	return {
		id: "job-1",
		job_type: "application.sdk_update",
		payload_version: 1,
		resource_type: "application",
		resource_id: "app-1",
		resource_lock_key: "application:app-1",
		priority: 100,
		title: "Update SDK",
		execution_backend: "kubernetes",
		requested_by_user_id: "user-1",
		requested_by_name: "Ada",
		status: "running",
		progress: { current: 0, total: 1, percent: 0 },
		revision: 1,
		attempt: 1,
		max_attempts: 1,
		can_cancel: false,
		created_at: "2026-09-12T12:00:00Z",
		updated_at: "2026-09-12T12:00:00Z",
		...overrides,
	};
}

function wrapper(queryClient: QueryClient) {
	return ({ children }: { children: ReactNode }) => (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
}

describe("useApplicationSdkUpdateJobs", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mocks.callback = undefined;
	});

	it("tracks active SDK update jobs and invalidates application queries on terminal updates", () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
		const { result } = renderHook(() => useApplicationSdkUpdateJobs(), {
			wrapper: wrapper(queryClient),
		});

		expect(webSocketService.onAnyPlatformJobUpdate).toHaveBeenCalledOnce();

		act(() => {
			mocks.callback?.(makeJob({ status: "running" }));
		});
		expect(result.current.hasUpdateState("app-1")).toBe(true);
		expect(result.current.getUpdateState("app-1")).toBe("updating");
		expect(result.current.isAnyUpdating(["app-1", "app-2"])).toBe(true);

		act(() => {
			mocks.callback?.(makeJob({ status: "succeeded" }));
		});
		expect(result.current.getUpdateState("app-1")).toBe("idle");
		expect(result.current.isAnyUpdating(["app-1", "app-2"])).toBe(false);
		expect(invalidateSpy).toHaveBeenCalledWith({
			queryKey: ["get", "/api/applications"],
		});
	});

	it("seeds queued jobs returned from enqueue responses", () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const { result } = renderHook(() => useApplicationSdkUpdateJobs(), {
			wrapper: wrapper(queryClient),
		});

		act(() => {
			result.current.trackAccepted([
				{
					application_id: "app-2",
					job_id: "job-2",
					status: "queued",
					reused: false,
					notification_id: null,
				},
			]);
		});

		expect(result.current.getUpdateState("app-2")).toBe("queued");
		expect(result.current.hasUpdateState("app-2")).toBe(true);
		expect(result.current.isAnyUpdating(["app-1", "app-2"])).toBe(true);
	});

	it("treats action-required SDK updates as terminal failures", () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
		const { result } = renderHook(() => useApplicationSdkUpdateJobs(), {
			wrapper: wrapper(queryClient),
		});

		act(() => {
			mocks.callback?.(makeJob({ status: "requires_action" }));
		});

		expect(result.current.getUpdateState("app-1")).toBe("failed");
		expect(invalidateSpy).toHaveBeenCalledWith({
			queryKey: ["get", "/api/applications"],
		});
	});

	it("ignores application jobs that are not the SDK update job type", () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const { result } = renderHook(() => useApplicationSdkUpdateJobs(), {
			wrapper: wrapper(queryClient),
		});

		act(() => {
			mocks.callback?.(
				makeJob({
					job_type: "application.publish",
					title: "Publish SDK demo app",
					status: "running",
				}),
			);
		});

		expect(result.current.getUpdateState("app-1")).toBe("idle");
		expect(result.current.hasUpdateState("app-1")).toBe(false);
	});
});
