// @vitest-environment happy-dom

import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

type PublishCallback = (update: {
	type: "app_published";
	appId: string;
	newVersionId: string;
	userId: string;
	userName: string;
	timestamp: string;
}) => void;

const ws = vi.hoisted(() => {
	const state = {
		published: undefined as PublishCallback | undefined,
	};
	return {
		state,
		connectToAppDraft: vi.fn().mockResolvedValue(undefined),
		connectToAppLive: vi.fn().mockResolvedValue(undefined),
		onAppCodeFileUpdate: vi.fn().mockReturnValue(vi.fn()),
		onAppPublished: vi.fn((_appId: string, callback: PublishCallback) => {
			state.published = callback;
			return vi.fn();
		}),
		unsubscribe: vi.fn(),
	};
});

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connectToAppDraft: ws.connectToAppDraft,
		connectToAppLive: ws.connectToAppLive,
		onAppCodeFileUpdate: ws.onAppCodeFileUpdate,
		onAppPublished: ws.onAppPublished,
		unsubscribe: ws.unsubscribe,
	},
}));

import { useAppCodeUpdates } from "./useAppCodeUpdates";

beforeEach(() => {
	vi.clearAllMocks();
	ws.state.published = undefined;
});

describe("useAppCodeUpdates", () => {
	it("refreshes application metadata from the existing publish WebSocket event", async () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const invalidate = vi.spyOn(queryClient, "invalidateQueries");
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>
				{children}
			</QueryClientProvider>
		);

		renderHook(
			() => useAppCodeUpdates({ appId: "app-1" }),
			{ wrapper },
		);

		await waitFor(() => expect(ws.onAppPublished).toHaveBeenCalledOnce());
		act(() => {
			ws.state.published?.({
				type: "app_published",
				appId: "app-1",
				newVersionId: "version-2",
				userId: "user-1",
				userName: "Dev",
				timestamp: new Date().toISOString(),
			});
		});

		await waitFor(() => expect(invalidate).toHaveBeenCalledOnce());
		const predicate = invalidate.mock.calls[0]?.[0]?.predicate;
		expect(predicate?.({ queryKey: ["get", "/api/applications"] } as never)).toBe(
			true,
		);
		expect(
			predicate?.({
				queryKey: ["get", "/api/applications/{slug}"],
			} as never),
		).toBe(true);
	});
});
