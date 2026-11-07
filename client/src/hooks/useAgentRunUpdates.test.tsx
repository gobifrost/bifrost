import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useAgentRunUpdates } from "./useAgentRunUpdates";

const { connectInBackground, unsubscribe, onAgentRunUpdate } = vi.hoisted(() => {
	const unsubscribe = vi.fn();
	return {
		connectInBackground: vi.fn(), unsubscribe,
		onAgentRunUpdate: vi.fn(() => unsubscribe),
	};
});
vi.mock("@/services/websocket", () => ({
	webSocketService: { connectInBackground, onAgentRunUpdate },
}));

it("requests the shared background connection and removes its listener on unmount", () => {
	const client = new QueryClient();
	const { unmount } = renderHook(() => useAgentRunUpdates({ agentId: "agent", runId: "run" }), {
		wrapper: ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={client}>{children}</QueryClientProvider>
		),
	});
	try {
		expect(connectInBackground).toHaveBeenCalledExactlyOnceWith(["agent-runs", "agent-run:run"]);
		expect(onAgentRunUpdate).toHaveBeenCalledTimes(1);
	} finally {
		unmount();
		client.clear();
	}
	expect(unsubscribe).toHaveBeenCalledTimes(1);
});
