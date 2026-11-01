import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState, type ReactNode } from "react";

const mock = vi.hoisted(() => ({
	connect: vi.fn(),
	unsubscribe: vi.fn(),
	onHistoryUpdate: vi.fn(),
	onExecutionUpdate: vi.fn(),
	onExecutionLog: vi.fn(),
	onNewExecution: vi.fn(),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: mock.connect,
		unsubscribe: mock.unsubscribe,
		isConnected: () => true,
		onHistoryUpdate: mock.onHistoryUpdate,
		onExecutionUpdate: mock.onExecutionUpdate,
		onExecutionLog: mock.onExecutionLog,
		onNewExecution: mock.onNewExecution,
	},
}));

import {
	useExecutionHistory,
	useExecutionStream,
	useNewExecutions,
} from "./useExecutionStream";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";

function Wrapper({ children }: { children: ReactNode }) {
	const [queryClient] = useState(
		() =>
			new QueryClient({
				defaultOptions: { queries: { retry: false } },
			}),
	);
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
	mock.onExecutionUpdate.mockReturnValue(() => undefined);
	mock.onExecutionLog.mockReturnValue(() => undefined);
	mock.onNewExecution.mockReturnValue(() => undefined);
	useExecutionStreamStore.setState({ streams: {} });
});

const subscriptions = [
	{
		name: "execution stream",
		useSubscription: () => useExecutionStream({ executionId: "run-1" }),
		listener: mock.onExecutionUpdate,
	},
	{
		name: "new executions",
		useSubscription: () => useNewExecutions(),
		listener: mock.onNewExecution,
	},
	{
		name: "execution history",
		useSubscription: () =>
			useExecutionHistory({ scope: "GLOBAL", includeOthers: true }),
		listener: mock.onHistoryUpdate,
	},
];

describe.each(subscriptions)(
	"$name subscription lifecycle",
	({ useSubscription, listener }) => {
		it("does not register listeners when connection completes after unmount", async () => {
			let resolveConnection!: () => void;
			const connection = new Promise<void>((resolve) => {
				resolveConnection = resolve;
			});
			mock.connect.mockReturnValue(connection);
			const { unmount } = renderHook(useSubscription, {
				wrapper: Wrapper,
			});
			unmount();

			await act(async () => {
				resolveConnection();
				await connection;
			});

			expect(listener).not.toHaveBeenCalled();
			expect(mock.onExecutionLog).not.toHaveBeenCalled();
		});

		it("registers while active and removes its listener on unmount", async () => {
			const unsubscribe = vi.fn();
			listener.mockReturnValue(unsubscribe);
			const { unmount } = renderHook(useSubscription, {
				wrapper: Wrapper,
			});
			await waitFor(() => expect(listener).toHaveBeenCalledOnce());

			unmount();

			expect(unsubscribe).toHaveBeenCalledOnce();
		});
	},
);

describe("useExecutionHistory", () => {
	it("keeps only the current scope listener when an earlier connection resolves last", async () => {
		let resolveOldConnection!: () => void;
		const oldConnection = new Promise<void>((resolve) => {
			resolveOldConnection = resolve;
		});
		mock.connect.mockReturnValueOnce(oldConnection);
		const { rerender } = renderHook(
			({ scope }) => useExecutionHistory({ scope, includeOthers: true }),
			{ initialProps: { scope: "org-1" }, wrapper: Wrapper },
		);
		rerender({ scope: "org-2" });
		await waitFor(() =>
			expect(mock.onHistoryUpdate).toHaveBeenCalledOnce(),
		);

		await act(async () => {
			resolveOldConnection();
			await oldConnection;
		});

		expect(mock.onHistoryUpdate).toHaveBeenCalledOnce();
	});

	it("uses the all-runs stream for a role with executions.read.all", async () => {
		renderHook(
			() =>
				useExecutionHistory({
					scope: "org-1",
					includeOthers: true,
					userId: "user-1",
				}),
			{ wrapper: Wrapper },
		);

		await waitFor(() =>
			expect(mock.connect).toHaveBeenCalledWith(["history:GLOBAL"]),
		);
	});
});

describe("useExecutionStream", () => {
	it("does not overwrite a retained stream with a connection failure after unmount", async () => {
		let rejectConnection!: (error: Error) => void;
		const connection = new Promise<void>((_resolve, reject) => {
			rejectConnection = reject;
		});
		mock.connect.mockReturnValue(connection);
		const { unmount } = renderHook(() =>
			useExecutionStream({ executionId: "run-1" }),
		);
		unmount();

		await act(async () => {
			rejectConnection(new Error("Connection failed"));
			await connection.catch(() => undefined);
		});

		expect(
			useExecutionStreamStore.getState().streams["run-1"].error,
		).toBeUndefined();
	});
});
