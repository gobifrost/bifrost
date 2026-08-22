import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: vi.fn() },
}));
vi.mock("@/hooks/useExecutions", () => ({ getExecution: vi.fn() }));
vi.mock("@/lib/app-sdk/execution-stream", () => ({
	subscribeToExecution: vi.fn(),
}));

import { apiClient } from "@/lib/api-client";
import { getExecution } from "@/hooks/useExecutions";
import {
	subscribeToExecution,
	type ExecutionStreamEvent,
} from "@/lib/app-sdk/execution-stream";
import { useWorkflowMutation } from "./useWorkflowMutation";

describe("useWorkflowMutation", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("uses the reconnecting SDK stream and reconciles a result after reconnect", async () => {
		(apiClient.POST as Mock).mockResolvedValue({
			data: { execution_id: "exec-1", status: "Running" },
			error: undefined,
		});
		(getExecution as Mock).mockResolvedValue({ status: "Running" });

		let onEvent: ((event: ExecutionStreamEvent) => void) | undefined;
		let onSocketDown: (() => void) | undefined;
		const unsubscribe = vi.fn();
		(subscribeToExecution as Mock).mockImplementation(
			(
				_executionId: string,
				eventCallback: (event: ExecutionStreamEvent) => void,
				socketDownCallback: () => void,
			) => {
				onEvent = eventCallback;
				onSocketDown = socketDownCallback;
				return unsubscribe;
			},
		);

		const { result } = renderHook(() =>
			useWorkflowMutation<{ value: number }>("workflow-1"),
		);
		let executionPromise!: Promise<{ value: number }>;
		act(() => {
			executionPromise = result.current.execute({ input: true });
		});

		await waitFor(() => {
			expect(subscribeToExecution).toHaveBeenCalledWith(
				"exec-1",
				expect.any(Function),
				expect.any(Function),
			);
			expect(getExecution).toHaveBeenCalledTimes(1);
		});

		// The shared stream reconnects automatically. While it is down the V1 hook
		// polls; its next `ready` event is the post-reconnect subscribe ack.
		act(() => onSocketDown?.());
		(getExecution as Mock).mockResolvedValue({
			status: "Success",
			result: { value: 42 },
			error_message: null,
		});
		act(() => onEvent?.({ type: "ready" }));

		await expect(executionPromise).resolves.toEqual({ value: 42 });
		await waitFor(() => {
			expect(result.current.data).toEqual({ value: 42 });
			expect(result.current.isLoading).toBe(false);
			expect(unsubscribe).toHaveBeenCalledOnce();
		});
	});
});
