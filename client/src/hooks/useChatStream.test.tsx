// @vitest-environment happy-dom

import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatProjection } from "@/lib/chat-runtime";

type ChatCallback = (event: Record<string, unknown>) => void;
type ConnectionCallback = (connected: boolean) => void;

const mocks = vi.hoisted(() => {
	const callbacks = {
		chat: undefined as ChatCallback | undefined,
		connection: undefined as ConnectionCallback | undefined,
	};
	const generatedIds: string[] = [];
	const store = {
		projectionsByConversation: {} as Record<string, ChatProjection>,
		applyChatRunEvent: vi.fn(),
		applyChatRunEvents: vi.fn(),
		hydrateConversationProjection: vi.fn(),
		stageOptimisticUserTurn: vi.fn(),
		setStreamError: vi.fn(),
	};
	return { callbacks, generatedIds, store };
});

vi.mock("@/stores/chatStore", () => ({
	useChatStore: Object.assign(
		(selector?: (state: typeof mocks.store) => unknown) =>
			selector ? selector(mocks.store) : mocks.store,
		{ getState: () => mocks.store },
	),
}));

vi.mock("@/lib/chat-utils", () => ({
	generateMessageId: vi.fn(
		() => mocks.generatedIds.shift() ?? "generated-id",
	),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/services/chatRuns", () => ({
	createChatRun: vi.fn(),
	getChatRunState: vi.fn(),
	cancelChatRun: vi.fn(),
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		isConnected: vi.fn(() => true),
		connectToChat: vi.fn().mockResolvedValue(undefined),
		onChatStream: vi.fn((_id: string, callback: ChatCallback) => {
			mocks.callbacks.chat = callback;
			return vi.fn();
		}),
		onConnectionStatusChange: vi.fn((callback: ConnectionCallback) => {
			mocks.callbacks.connection = callback;
			return vi.fn();
		}),
		onPlatformJobUpdate: vi.fn(() => vi.fn()),
	},
}));

import {
	cancelChatRun,
	createChatRun,
	getChatRunState,
} from "@/services/chatRuns";
import { webSocketService } from "@/services/websocket";
import { useChatStream } from "./useChatStream";

function makeState(status: string | null = null) {
	return {
		conversation: {
			id: "conversation-1",
			user_id: "user-1",
			channel: "chat",
			is_active: true,
			created_at: "2026-09-02T00:00:00Z",
			updated_at: "2026-09-02T00:00:00Z",
		},
		active_run: status
			? {
					id: "run-1",
					conversation_id: "conversation-1",
					status,
					created_at: "2026-09-02T00:00:00Z",
				}
			: null,
		messages: [],
		events: [],
		latest_sequence: status ? 1 : 0,
	};
}

function wrapper() {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return ({ children }: { children: ReactNode }) => (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	mocks.callbacks.chat = undefined;
	mocks.callbacks.connection = undefined;
	mocks.generatedIds.splice(0);
	mocks.store.projectionsByConversation = {};
	vi.mocked(webSocketService.isConnected).mockReturnValue(true);
	vi.mocked(webSocketService.connectToChat).mockResolvedValue(undefined);
	vi.mocked(createChatRun).mockResolvedValue({
		run_id: "run-1",
		status: "queued",
	} as never);
	vi.mocked(getChatRunState).mockResolvedValue(makeState() as never);
	vi.mocked(cancelChatRun).mockResolvedValue({
		run_id: "run-1",
		status: "cancelled",
	});
});

describe("useChatStream", () => {
	it("subscribes before hydrating the durable conversation state", async () => {
		renderHook(() => useChatStream({ conversationId: "conversation-1" }), {
			wrapper: wrapper(),
		});

		await waitFor(() =>
			expect(webSocketService.connectToChat).toHaveBeenCalledWith(
				"conversation-1",
			),
		);
		expect(webSocketService.onChatStream).toHaveBeenCalledWith(
			"conversation-1",
			expect.any(Function),
		);
		expect(getChatRunState).toHaveBeenCalledWith("conversation-1");
		expect(mocks.store.hydrateConversationProjection).toHaveBeenCalledWith(
			"conversation-1",
			expect.objectContaining({ conversation_id: "conversation-1" }),
			[],
		);
	});

	it("does not probe the server while a first-turn command is still pending", async () => {
		mocks.store.projectionsByConversation = {
			"conversation-1": {
				conversation_id: "conversation-1",
				messages: [],
				system_events: [],
				runs: {
					"run-1": {
						run_id: "run-1",
						conversation_id: "conversation-1",
						status: "pending",
						last_sequence: 0,
						last_event_id: null,
						applied_event_ids: [],
						user_message_id: "user-1",
						assistant_message_id: null,
						streaming_message_id: null,
					},
				},
				run_order: ["run-1"],
				active_run_id: "run-1",
				last_sequence: 0,
			},
		};

		renderHook(() => useChatStream({ conversationId: "conversation-1" }), {
			wrapper: wrapper(),
		});

		expect(webSocketService.onChatStream).toHaveBeenCalledWith(
			"conversation-1",
			expect.any(Function),
		);
		expect(webSocketService.connectToChat).not.toHaveBeenCalled();
		expect(getChatRunState).not.toHaveBeenCalled();
	});

	it("stages immediately, then creates the server run with the same IDs", async () => {
		mocks.generatedIds.push("run-1", "user-message-1");
		const { result } = renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper: wrapper() },
		);

		await act(async () => {
			await result.current.sendMessage(
				"hello",
				undefined,
				[
					{
						id: "attachment-1",
						filename: "brief.pdf",
						content_type: "application/pdf",
						size_bytes: 10,
						kind: "attachment",
					},
				],
				"profile-pro",
			);
		});

		expect(mocks.store.stageOptimisticUserTurn).toHaveBeenCalledWith(
			"conversation-1",
			expect.objectContaining({
				run_id: "run-1",
				user_message_id: "user-message-1",
				local_id: "user-message-1",
				content: "hello",
				model: "profile-pro",
			}),
		);
		expect(createChatRun).toHaveBeenCalledWith({
			conversation_id: "conversation-1",
			content: "hello",
			client_run_id: "run-1",
			user_message_id: "user-message-1",
			attachment_ids: ["attachment-1"],
			model_profile_id: "profile-pro",
		});
		expect(
			mocks.store.stageOptimisticUserTurn.mock.invocationCallOrder[0],
		).toBeLessThan(vi.mocked(createChatRun).mock.invocationCallOrder[0]);
	});

	it("batches token events before updating the projection", async () => {
		renderHook(() => useChatStream({ conversationId: "conversation-1" }), {
			wrapper: wrapper(),
		});
		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());

		const first = {
			type: "chat_run_event",
			event_id: "event-1",
			sequence: 1,
			conversation_id: "conversation-1",
			run_id: "run-1",
			payload: { type: "delta", content: "Hel" },
		};
		const second = {
			...first,
			event_id: "event-2",
			sequence: 2,
			payload: { type: "delta", content: "lo" },
		};
		act(() => {
			mocks.callbacks.chat?.(first);
			mocks.callbacks.chat?.(second);
		});
		expect(mocks.store.applyChatRunEvents).not.toHaveBeenCalled();

		await act(
			() => new Promise((resolve) => window.setTimeout(resolve, 40)),
		);
		expect(mocks.store.applyChatRunEvents).toHaveBeenCalledWith(
			"conversation-1",
			[first, second],
		);
	});

	it("replays state again after the websocket reconnects", async () => {
		renderHook(() => useChatStream({ conversationId: "conversation-1" }), {
			wrapper: wrapper(),
		});
		await waitFor(() => expect(mocks.callbacks.connection).toBeDefined());
		await waitFor(() => expect(getChatRunState).toHaveBeenCalled());
		vi.mocked(getChatRunState).mockClear();

		act(() => {
			mocks.callbacks.connection?.(false);
			mocks.callbacks.connection?.(true);
		});

		await waitFor(() =>
			expect(getChatRunState).toHaveBeenCalledWith("conversation-1"),
		);
	});

	it("does not resubscribe or rehydrate when callback props change identity", async () => {
		const firstOnError = vi.fn();
		const secondOnError = vi.fn();
		const { rerender } = renderHook(
			({ onError }: { onError: (message: string) => void }) =>
				useChatStream({
					conversationId: "conversation-1",
					onError,
				}),
			{
				wrapper: wrapper(),
				initialProps: { onError: firstOnError },
			},
		);

		await waitFor(() => expect(getChatRunState).toHaveBeenCalledTimes(1));
		rerender({ onError: secondOnError });

		await act(async () => Promise.resolve());
		expect(webSocketService.onChatStream).toHaveBeenCalledTimes(1);
		expect(getChatRunState).toHaveBeenCalledTimes(1);

		act(() => {
			mocks.callbacks.chat?.({
				type: "chat_run_event",
				event_id: "event-error",
				sequence: 1,
				conversation_id: "conversation-1",
				run_id: "run-1",
				payload: { type: "error", error: "latest callback" },
			});
		});

		expect(firstOnError).not.toHaveBeenCalled();
		expect(secondOnError).toHaveBeenCalledWith("latest callback");
	});

	it("restores the chat callback after Strict Mode replays mount effects", async () => {
		renderHook(() => useChatStream({ conversationId: "conversation-1" }), {
			wrapper: wrapper(),
			reactStrictMode: true,
		});

		await waitFor(() =>
			expect(webSocketService.onChatStream).toHaveBeenCalledTimes(2),
		);
		expect(mocks.callbacks.chat).toEqual(expect.any(Function));
	});

	it("cancels the active durable run and refreshes its state", async () => {
		mocks.store.projectionsByConversation = {
			"conversation-1": {
				conversation_id: "conversation-1",
				messages: [],
				system_events: [],
				runs: {
					"run-1": {
						run_id: "run-1",
						conversation_id: "conversation-1",
						status: "running",
						last_sequence: 1,
						last_event_id: null,
						applied_event_ids: [],
						user_message_id: "user-1",
						assistant_message_id: null,
						streaming_message_id: null,
					},
				},
				run_order: ["run-1"],
				active_run_id: "run-1",
				last_sequence: 1,
			},
		};
		vi.mocked(getChatRunState).mockResolvedValue(
			makeState("cancelled") as never,
		);
		const { result } = renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper: wrapper() },
		);

		await act(async () => {
			await result.current.stopStreaming();
		});

		expect(cancelChatRun).toHaveBeenCalledWith("run-1");
		expect(getChatRunState).toHaveBeenCalledWith("conversation-1");
	});
});
