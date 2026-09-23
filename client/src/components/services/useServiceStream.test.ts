import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({
	connected: false,
	callbacks: new Map<string, (log: unknown) => void>(),
	connectToService: vi.fn(),
	unsubscribe: vi.fn(),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connectToService: (...args: unknown[]) =>
			mock.connectToService(...args),
		unsubscribe: (...args: unknown[]) => mock.unsubscribe(...args),
		isConnected: () => mock.connected,
		onServiceLog: (serviceId: string, callback: (log: unknown) => void) => {
			mock.callbacks.set(serviceId, callback);
			return () => mock.callbacks.delete(serviceId);
		},
	},
}));

import { useServiceStream } from "./useServiceStream";

beforeEach(() => {
	mock.connected = false;
	mock.callbacks.clear();
	mock.connectToService.mockReset().mockResolvedValue(undefined);
	mock.unsubscribe.mockReset().mockResolvedValue(undefined);
});

function emit(serviceId: string, message: string) {
	act(() => {
		mock.callbacks.get(serviceId)?.({
			serviceId,
			attemptId: "att-1",
			level: "INFO",
			message,
			timestamp: "2026-09-21T12:00:00+00:00",
		});
	});
}

describe("useServiceStream", () => {
	it("subscribes to service:{id} and accumulates live lines", async () => {
		mock.connected = true;
		const { result } = renderHook(() =>
			useServiceStream({ serviceId: "svc-1" }),
		);

		await waitFor(() =>
			expect(mock.connectToService).toHaveBeenCalledWith("svc-1"),
		);
		await waitFor(() => expect(result.current.isConnected).toBe(true));

		emit("svc-1", "hello live");
		emit("svc-1", "second line");
		expect(result.current.streamingLogs).toEqual([
			{
				level: "INFO",
				message: "hello live",
				timestamp: "2026-09-21T12:00:00+00:00",
			},
			{
				level: "INFO",
				message: "second line",
				timestamp: "2026-09-21T12:00:00+00:00",
			},
		]);
	});

	it("unsubscribes from the channel on unmount", async () => {
		mock.connected = true;
		const { unmount } = renderHook(() =>
			useServiceStream({ serviceId: "svc-1" }),
		);
		await waitFor(() =>
			expect(mock.connectToService).toHaveBeenCalledWith("svc-1"),
		);
		unmount();
		expect(mock.unsubscribe).toHaveBeenCalledWith("service:svc-1");
	});

	it("stays idle without a service id", () => {
		renderHook(() => useServiceStream({ serviceId: undefined }));
		expect(mock.connectToService).not.toHaveBeenCalled();
	});

	it("reports disconnected when the socket is down", async () => {
		mock.connected = false;
		const { result } = renderHook(() =>
			useServiceStream({ serviceId: "svc-1" }),
		);
		await waitFor(() => expect(result.current.isConnected).toBe(false));
		expect(result.current.streamingLogs).toEqual([]);
	});
});
