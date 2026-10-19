import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	getNotifications: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		user: { id: "notification-test-user" },
		isAuthenticated: true,
	}),
}));

vi.mock("@/services/notifications", () => ({
	getNotifications: mocks.getNotifications,
	dismissNotification: vi.fn(),
}));

import { webSocketService } from "@/services/websocket";
import { useNotifications } from "./useNotifications";

const service = webSocketService as unknown as {
	ws: WebSocket | null;
	handleMessage(message: unknown): void;
};

afterEach(async () => {
	await webSocketService.disconnect();
});

describe("useNotifications subscription lifecycle", () => {
	it("retains both notification channels when StrictMode remounts before unsubscribe acknowledgements", async () => {
		const socket = {
			readyState: WebSocket.OPEN,
			send: vi.fn(),
			close: vi.fn(),
		};
		service.ws = socket as unknown as WebSocket;
		const channels = [
			"notification:notification-test-user",
			"notification:admins",
		];
		for (const channel of channels) {
			await webSocketService.subscribe(channel);
			service.handleMessage({ type: "subscribed", channel });
		}
		socket.send.mockClear();

		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const { unmount } = renderHook(() => useNotifications(), {
			reactStrictMode: true,
			wrapper: ({ children }: { children: ReactNode }) => (
				<QueryClientProvider client={queryClient}>
					{children}
				</QueryClientProvider>
			),
		});
		try {
			await waitFor(() =>
				expect(mocks.getNotifications).toHaveBeenCalled(),
			);
			for (const channel of channels) {
				expect(socket.send).toHaveBeenCalledWith(
					JSON.stringify({ type: "unsubscribe", channel }),
				);
			}

			act(() => {
				for (const channel of channels) {
					service.handleMessage({ type: "unsubscribed", channel });
				}
			});

			const messages = socket.send.mock.calls.map(
				([message]) =>
					JSON.parse(message as string) as {
						type: string;
						channel?: string;
						channels?: string[];
					},
			);
			for (const channel of channels) {
				const channelMessages = messages.filter(
					(message) =>
						message.channel === channel ||
						message.channels?.includes(channel),
				);
				expect(channelMessages.at(-1)).toEqual({
					type: "subscribe",
					channels: [channel],
				});
			}
		} finally {
			unmount();
			queryClient.clear();
		}
	});
});
