import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAgentPlatformUpdates } from "./useAgentPlatformUpdates";
const listeners = vi.hoisted(() => ({
	run: undefined as undefined | ((event: { run_id: string }) => void),
	journal: undefined as undefined | ((id: string) => void),
	reconnect: undefined as undefined | ((connected: boolean) => void),
	off: vi.fn(),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: vi.fn().mockResolvedValue(undefined),
		onAgentRunUpdate: vi.fn((callback) => {
			listeners.run = callback;
			return listeners.off;
		}),
		onAgentJournalAppended: vi.fn((callback) => {
			listeners.journal = callback;
			return listeners.off;
		}),
		onPlatformJobUpdate: vi.fn(() => listeners.off),
		onConnectionStatusChange: vi.fn((callback) => {
			listeners.reconnect = callback;
			return listeners.off;
		}),
	},
}));
describe("Agent Platform realtime", () => {
	it("invalidates authoritative reads for matching events and reconnect, and cleans up", async () => {
		const client = new QueryClient();
		const invalidate = vi.spyOn(client, "invalidateQueries");
		const wrapper = ({ children }: { children: React.ReactNode }) => (
			<QueryClientProvider client={client}>
				{children}
			</QueryClientProvider>
		);
		const { unmount } = renderHook(
			() => useAgentPlatformUpdates("run", "job"),
			{ wrapper },
		);
		await Promise.resolve();
		invalidate.mockClear();
		listeners.run?.({ run_id: "different" });
		expect(invalidate).not.toHaveBeenCalled();
		listeners.journal?.("run");
		listeners.run?.({ run_id: "run" });
		listeners.reconnect?.(true);
		expect(
			invalidate.mock.calls.filter(
				([options]) => options?.queryKey?.[0] === "agent-platform",
			),
		).toHaveLength(3);
		expect(invalidate).toHaveBeenCalledWith({
			queryKey: [
				"get",
				"/api/agent-runs/{run_id}",
				{ params: { path: { run_id: "run" } } },
			],
		});
		unmount();
		expect(listeners.off).toHaveBeenCalledTimes(4);
	});
});
