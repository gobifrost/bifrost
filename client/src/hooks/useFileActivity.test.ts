import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useFileActivityStore } from "@/stores/fileActivityStore";

const mock = vi.hoisted(() => ({
	canPlatformRead: true,
	canRepositoryRead: true,
	activityListener: undefined as
		| ((event: {
				type: "file_push" | "watch_start" | "watch_stop";
				user_id: string;
				user_name: string;
				prefix: string;
				file_count: number;
				is_watch: boolean;
				timestamp: string;
			}) => void)
		| undefined,
	connectionListeners: new Set<(connected: boolean) => void>(),
	connected: true,
	subscribe: vi.fn(),
	unsubscribe: vi.fn(),
	authFetch: vi.fn(),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: (permission: string) =>
			permission === "platform.read"
				? mock.canPlatformRead
				: mock.canRepositoryRead,
	}),
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: mock.authFetch,
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		subscribe: mock.subscribe,
		unsubscribe: mock.unsubscribe,
		isConnected: () => mock.connected,
		onFileActivity: (listener: typeof mock.activityListener) => {
			mock.activityListener = listener;
			return () => {
				mock.activityListener = undefined;
			};
		},
		onConnectionStatusChange: (listener: (connected: boolean) => void) => {
			mock.connectionListeners.add(listener);
			return () => mock.connectionListeners.delete(listener);
		},
	},
}));

import { useFileActivity } from "./useFileActivity";

function response(watchers: unknown[]) {
	return {
		ok: true,
		json: async () => ({ watchers }),
	} as Response;
}

function reportConnection(connected: boolean) {
	act(() => {
		mock.connected = connected;
		mock.connectionListeners.forEach((listener) => listener(connected));
	});
}

beforeEach(() => {
	mock.canPlatformRead = true;
	mock.canRepositoryRead = true;
	mock.activityListener = undefined;
	mock.connectionListeners.clear();
	mock.connected = true;
	mock.subscribe.mockReset().mockResolvedValue(undefined);
	mock.unsubscribe.mockReset().mockResolvedValue(undefined);
	mock.authFetch.mockReset().mockResolvedValue(response([]));
	useFileActivityStore.setState({ recentPushes: [], activeWatchers: [] });
});

describe("useFileActivity", () => {
	it("subscribes with platform.read and snapshots watchers on initial connect and reconnect", async () => {
		mock.authFetch
			.mockResolvedValueOnce(
				response([
					{
						user_id: "one",
						user_name: "Alex",
						prefix: "workflows",
						started_at: "2026-10-17T12:00:00Z",
					},
				]),
			)
			.mockResolvedValueOnce(response([]));
		renderHook(() => useFileActivity());

		await waitFor(() =>
			expect(mock.subscribe).toHaveBeenCalledWith("file-activity"),
		);
		await waitFor(() =>
			expect(useFileActivityStore.getState().activeWatchers).toHaveLength(1),
		);

		reportConnection(false);
		expect(useFileActivityStore.getState().activeWatchers).toEqual([]);
		reportConnection(true);
		await waitFor(() => expect(mock.authFetch).toHaveBeenCalledTimes(2));
	});

	it("keeps stream delivery but does not fetch watchers without repository.read", async () => {
		mock.canRepositoryRead = false;
		renderHook(() => useFileActivity());

		await waitFor(() =>
			expect(mock.subscribe).toHaveBeenCalledWith("file-activity"),
		);
		expect(mock.authFetch).not.toHaveBeenCalled();
		act(() =>
			mock.activityListener?.({
				type: "watch_start",
				user_id: "one",
				user_name: "Alex",
				prefix: "workflows",
				file_count: 0,
				is_watch: true,
				timestamp: "2026-10-17T12:00:00Z",
			}),
		);
		expect(useFileActivityStore.getState().activeWatchers).toHaveLength(1);
	});

	it("cleans store and subscriptions when platform.read is revoked", async () => {
		const { rerender, unmount } = renderHook(() => useFileActivity());
		await waitFor(() => expect(mock.subscribe).toHaveBeenCalledTimes(1));
		act(() =>
			mock.activityListener?.({
				type: "file_push",
				user_id: "other",
				user_name: "Alex",
				prefix: "workflows",
				file_count: 1,
				is_watch: false,
				timestamp: new Date().toISOString(),
			}),
		);
		mock.canPlatformRead = false;
		rerender();

		await waitFor(() => expect(mock.unsubscribe).toHaveBeenCalledWith("file-activity"));
		expect(useFileActivityStore.getState().recentPushes).toEqual([]);
		expect(useFileActivityStore.getState().activeWatchers).toEqual([]);
		unmount();
		expect(mock.connectionListeners.size).toBe(0);
	});
});
