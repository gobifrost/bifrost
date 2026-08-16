// @vitest-environment happy-dom

import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

type ChatCallback = (chunk: Record<string, unknown>) => void;
type JobCallback = (update: Record<string, unknown>) => void;

const mocks = vi.hoisted(() => {
	const callbacks = {
		chat: undefined as ChatCallback | undefined,
		job: undefined as JobCallback | undefined,
	};
	const unsubscribeJob = vi.fn();
	const store = {
		isStreaming: false,
		startStreaming: vi.fn(),
		completeStream: vi.fn(),
		setStreamError: vi.fn(),
		resetStream: vi.fn(),
		addSystemEvent: vi.fn(),
		addMessage: vi.fn(),
		setTodos: vi.fn(),
		updateMessage: vi.fn(),
		messagesByConversation: {} as Record<string, Array<Record<string, unknown>>>,
		streamingMessageIds: {} as Record<string, string | null>,
		setStreamingMessageIdForConversation: vi.fn(),
		mapLocalIdToServerId: vi.fn(),
		setMessages: vi.fn(),
	};
	return { callbacks, store, unsubscribeJob };
});

vi.mock("@/stores/chatStore", () => ({
	useChatStore: Object.assign(() => mocks.store, {
		getState: () => mocks.store,
	}),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

vi.mock("@/services/websocket", () => ({
	webSocketService: {
		isConnected: vi.fn(() => true),
		connectToChat: vi.fn().mockResolvedValue(undefined),
		onChatStream: vi.fn((_id: string, callback: ChatCallback) => {
			mocks.callbacks.chat = callback;
			return vi.fn();
		}),
		onConnectionStatusChange: vi.fn(() => vi.fn()),
		onPlatformJobUpdate: vi.fn((_id: string, callback: JobCallback) => {
			mocks.callbacks.job = callback;
			return mocks.unsubscribeJob;
		}),
		sendChatMessage: vi.fn(() => true),
		sendChatAnswer: vi.fn(),
		sendChatStop: vi.fn(),
	},
}));

import { toast } from "sonner";
import { webSocketService } from "@/services/websocket";
import { useChatStream } from "./useChatStream";

beforeEach(() => {
	vi.clearAllMocks();
	mocks.callbacks.chat = undefined;
	mocks.callbacks.job = undefined;
	mocks.store.messagesByConversation = {};
	mocks.store.streamingMessageIds = {};
	mocks.store.setStreamingMessageIdForConversation.mockImplementation(
		(conversationId: string, messageId: string | null) => {
			mocks.store.streamingMessageIds = {
				...mocks.store.streamingMessageIds,
				[conversationId]: messageId,
			};
		},
	);
});

describe("useChatStream video jobs", () => {
	it("does not end an active run when the new conversation subscription mounts", async () => {
		const queryClient = new QueryClient();
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
		);

		renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper },
		);

		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());
		expect(mocks.store.resetStream).not.toHaveBeenCalled();
	});

	it("reconciles intermediate and final stream IDs around tool calls", async () => {
		const queryClient = new QueryClient();
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
		);
		renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper },
		);
		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());

		act(() => {
			mocks.callbacks.chat?.({
				type: "message_start",
				assistant_message_id: "assistant-summary",
			});
			mocks.callbacks.chat?.({
				type: "assistant_message_end",
				message_id: "assistant-progress",
			});
			mocks.callbacks.chat?.({
				type: "delta",
				content: "Final answer",
			});
		});

		expect(mocks.store.updateMessage).toHaveBeenCalledWith(
			"conversation-1",
			"assistant-summary",
			expect.objectContaining({
				id: "assistant-progress",
				isStreaming: false,
			}),
		);
		const finalSegment = mocks.store.addMessage.mock.calls.at(-1)?.[1] as {
			id: string;
		};
		expect(finalSegment.id).toBeTruthy();

		act(() => {
			mocks.callbacks.chat?.({
				type: "done",
				message_id: "assistant-summary",
				duration_ms: 2_400,
			});
		});

		expect(mocks.store.updateMessage).toHaveBeenCalledWith(
			"conversation-1",
			finalSegment.id,
			expect.objectContaining({
				id: "assistant-summary",
				isStreaming: false,
				duration_ms: 2_400,
			}),
		);
		expect(mocks.store.streamingMessageIds["conversation-1"]).toBeNull();
	});

	it("applies final duration after the text segment already ended", async () => {
		const queryClient = new QueryClient();
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
		);
		renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper },
		);
		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());

		act(() => {
			mocks.callbacks.chat?.({
				type: "message_start",
				assistant_message_id: "assistant-summary",
			});
			mocks.callbacks.chat?.({ type: "assistant_message_end" });
			mocks.callbacks.chat?.({
				type: "done",
				message_id: "assistant-summary",
				duration_ms: 8_500,
				token_count_input: 12,
				token_count_output: 3,
			});
		});

		expect(mocks.store.updateMessage).toHaveBeenCalledWith(
			"conversation-1",
			"assistant-summary",
			expect.objectContaining({ duration_ms: 8_500 }),
		);
	});

	it("adds an opaque artifact reference to the completed tool message", async () => {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>
				{children}
			</QueryClientProvider>
		);
		mocks.store.messagesByConversation = {
			"conversation-1": [{ id: "tool-message-1", attachments: [] }],
		};

		renderHook(
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper },
		);
		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());

		act(() => {
			mocks.callbacks.chat?.({
				type: "artifact_ready",
				message_id: "tool-message-1",
				artifact: {
					type: "bifrost_artifact",
					id: "artifact-1",
					filename: "Launch Brief.pdf",
					content_type: "application/pdf",
					size_bytes: 42,
				},
			});
		});

		expect(mocks.store.updateMessage).toHaveBeenCalledWith(
			"conversation-1",
			"tool-message-1",
			{
				attachments: [
					{
						id: "artifact-1",
						filename: "Launch Brief.pdf",
						content_type: "application/pdf",
						size_bytes: 42,
						kind: "artifact",
					},
				],
			},
		);
	});

	it("refreshes Chat and Artifacts when durable video generation finishes", async () => {
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
			() => useChatStream({ conversationId: "conversation-1" }),
			{ wrapper },
		);
		await waitFor(() => expect(mocks.callbacks.chat).toBeDefined());

		act(() => {
			mocks.callbacks.chat?.({
				type: "tool_result",
				conversation_id: "conversation-1",
				message_id: "tool-message-1",
				tool_result: {
					tool_call_id: "call-1",
					tool_name: "create_video_artifact",
					result: {
						type: "platform_job",
						kind: "video_generation",
						job_id: "job-1",
						conversation_id: "conversation-1",
					},
					duration_ms: 10,
				},
			});
		});

		expect(webSocketService.onPlatformJobUpdate).toHaveBeenCalledWith(
			"job-1",
			expect.any(Function),
		);

		act(() => {
			mocks.callbacks.job?.({ status: "succeeded" });
		});

		expect(invalidate).toHaveBeenCalledWith({
			queryKey: ["chat-artifacts"],
		});
		expect(mocks.store.updateMessage).toHaveBeenLastCalledWith(
			"conversation-1",
			"tool-message-1",
			expect.objectContaining({ tool_state: "completed" }),
		);
		expect(toast.success).toHaveBeenCalledWith("Video ready");
		expect(mocks.unsubscribeJob).toHaveBeenCalledOnce();
	});
});
