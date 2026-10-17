import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const mock = vi.hoisted(() => ({
	connect: vi.fn(),
	unsubscribe: vi.fn(),
	onHistoryUpdate: vi.fn(),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: mock.connect,
		unsubscribe: mock.unsubscribe,
		isConnected: () => true,
		onHistoryUpdate: mock.onHistoryUpdate,
	},
}));

import { useExecutionHistory } from "./useExecutionStream";

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	mock.connect.mockResolvedValue(undefined);
	mock.onHistoryUpdate.mockReturnValue(() => undefined);
});

describe("useExecutionHistory", () => {
	it("uses the all-runs stream for a role with executions.read.all", async () => {
		renderHook(
			() =>
				useExecutionHistory({
					scope: "org-1",
					includeOthers: true,
					userId: "user-1",
				}),
			{ wrapper },
		);

		await waitFor(() =>
			expect(mock.connect).toHaveBeenCalledWith(["history:GLOBAL"]),
		);
	});
});
