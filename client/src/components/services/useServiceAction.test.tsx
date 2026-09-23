import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const mocks = vi.hoisted(() => ({
	startService: vi.fn(),
	stopService: vi.fn(),
	restartService: vi.fn(),
	enableService: vi.fn(),
	disableService: vi.fn(),
}));

vi.mock("@/services/services", async (importOriginal) => ({
	...((await importOriginal()) as object),
	startService: (...args: unknown[]) => mocks.startService(...args),
	stopService: (...args: unknown[]) => mocks.stopService(...args),
	restartService: (...args: unknown[]) => mocks.restartService(...args),
	enableService: (...args: unknown[]) => mocks.enableService(...args),
	disableService: (...args: unknown[]) => mocks.disableService(...args),
}));

import {
	SERVICES_QUERY_KEY,
	serviceDetailQueryKey,
} from "@/services/services";
import {
	useServiceAction,
	type ServiceActionRequest,
} from "./useServiceAction";

function wrapper({ children }: { children: ReactNode }) {
	return (
		<QueryClientProvider
			client={
				new QueryClient({
					defaultOptions: {
						queries: { retry: false },
						mutations: { retry: false },
					},
				})
			}
		>
			{children}
		</QueryClientProvider>
	);
}

const request: ServiceActionRequest = {
	service: { id: "svc-1", workflow_name: "telegram_bridge" } as ServiceActionRequest["service"],
	action: "stop",
};

describe("useServiceAction", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("posts the stop action and invalidates list + detail queries", async () => {
		mocks.stopService.mockResolvedValue({ id: "svc-1" });
		const invalidateQueries = vi.fn();
		const { result } = renderHook(() => useServiceAction(), {
			wrapper: ({ children }: { children: ReactNode }) => {
				const client = new QueryClient({
					defaultOptions: {
						queries: { retry: false },
						mutations: { retry: false },
					},
				});
				client.invalidateQueries = invalidateQueries;
				return (
					<QueryClientProvider client={client}>
						{children}
					</QueryClientProvider>
				);
			},
		});

		await result.current.runAction(request);

		expect(mocks.stopService).toHaveBeenCalledExactlyOnceWith("svc-1");
		await waitFor(() =>
			expect(invalidateQueries).toHaveBeenCalledWith({
				queryKey: [...SERVICES_QUERY_KEY],
			}),
		);
		expect(invalidateQueries).toHaveBeenCalledWith({
			queryKey: serviceDetailQueryKey("svc-1"),
		});
		expect(toast.success).toHaveBeenCalledWith(
			"telegram_bridge stopped.",
		);
	});

	it("surfaces the error message through getErrorMessage when the action fails", async () => {
		mocks.stopService.mockRejectedValue(new Error("boom"));
		const { result } = renderHook(() => useServiceAction(), {
			wrapper,
		});

		await expect(result.current.runAction(request)).rejects.toThrow();
		await waitFor(() =>
			expect(toast.error).toHaveBeenCalledWith("boom"),
		);
	});

	it("falls back to the action-specific message for message-less failures", async () => {
		mocks.stopService.mockRejectedValue({});
		const { result } = renderHook(() => useServiceAction(), {
			wrapper,
		});

		await expect(result.current.runAction(request)).rejects.toThrow();
		await waitFor(() =>
			expect(toast.error).toHaveBeenCalledWith(
				"Could not stop the service.",
			),
		);
	});
});
