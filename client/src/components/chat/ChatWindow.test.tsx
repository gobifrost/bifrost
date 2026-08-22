/**
 * Component tests for ChatWindow.
 *
 * Covers:
 *   - No conversationId → initial empty state CTA
 *   - Empty conversation → "Start a conversation" or agent-specific greeting
 *   - Loading state renders skeleton rows
 *   - Messages from the hook render in the timeline
 *   - sendMessage gets forwarded to the ChatInput's onSend
 *
 * We stub the network + stream hooks and the store selectors so the test
 * stays deterministic.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, fireEvent, waitFor } from "@/test-utils";

// --- mocks --------------------------------------------------------------

const messagesRef: {
	data: Array<Record<string, unknown>> | undefined;
	isLoading: boolean;
} = { data: [], isLoading: false };

const streamRef = {
	sendMessage: vi.fn(),
	isStreaming: false,
	pendingQuestion: null as unknown,
	answerQuestion: vi.fn(),
	stopStreaming: vi.fn(),
};

const createConversationRef = {
	mutateAsync: vi.fn(),
	isPending: false,
};

vi.mock("@/hooks/useChat", () => ({
	useMessages: () => ({
		data: messagesRef.data,
		isLoading: messagesRef.isLoading,
	}),
	useCreateConversation: () => createConversationRef,
	useChatModelTiers: () => ({
		data: {
			tiers: [
				{
					id: "balanced",
					label: "Balanced",
					capabilities: {
						image_input: false,
						pdf_input: false,
						tool_calling: true,
						source: "verified",
						fingerprint: "test",
					},
				},
			],
			default_tier: "balanced",
		},
	}),
}));

vi.mock("@/hooks/useChatStream", () => ({
	useChatStream: () => streamRef,
}));

// chatStore: ChatWindow uses a few selectors. Return stable defaults.
const storeSelectors = {
	setActiveConversation: vi.fn(),
	setActiveAgent: vi.fn(),
	messagesByConversation: {} as Record<string, unknown[]>,
	systemEventsByConversation: {} as Record<string, unknown[]>,
	streamingMessageIds: {} as Record<string, string | null>,
	todos: [] as unknown[],
	getToolExecution: vi.fn(() => undefined),
};

vi.mock("@/stores/chatStore", () => ({
	useChatStore: <T,>(selector: (s: typeof storeSelectors) => T) =>
		selector(storeSelectors),
	useTodos: () => storeSelectors.todos,
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useNavigate: () => mockNavigate };
});

// Child components we don't need to exercise — stub to simple markers.
vi.mock("./ChatMessage", () => ({
	ChatMessage: ({ message }: { message: { content?: string | null } }) => (
		<div data-marker="chat-message">{message.content}</div>
	),
}));

vi.mock("./ChatInput", () => ({
	ChatInput: ({
		onSend,
		placeholder,
	}: {
		onSend: (m: string, files: File[], tier: "balanced") => void;
		placeholder?: string;
	}) => (
		<div>
			<input
				aria-label="chat input"
				placeholder={placeholder}
				onKeyDown={(e) => {
					if (e.key === "Enter") {
						onSend(
							(e.target as HTMLInputElement).value,
							[],
							"balanced",
						);
					}
				}}
			/>
		</div>
	),
}));

vi.mock("./ToolExecutionCard", () => ({
	ToolExecutionCard: () => <div data-marker="tool-card" />,
}));
vi.mock("./ToolExecutionBadge", () => ({
	ToolExecutionBadge: () => <div data-marker="tool-badge" />,
}));
vi.mock("./ToolExecutionGroup", () => ({
	ToolExecutionGroup: ({ children }: { children: React.ReactNode }) => (
		<div data-marker="tool-group">{children}</div>
	),
}));
vi.mock("./ChatSystemEvent", () => ({
	ChatSystemEvent: () => <div data-marker="sys-event" />,
}));
vi.mock("./AskUserQuestionCard", () => ({
	AskUserQuestionCard: () => <div data-marker="ask-user" />,
}));
vi.mock("./TodoList", () => ({
	TodoList: () => <div data-marker="todo-list" />,
}));

// integrateMessages: ChatWindow delegates API+local merge to this util.
// Pass through a concat to keep tests predictable.
vi.mock("@/lib/chat-utils", () => ({
	integrateMessages: (
		a: Array<Record<string, unknown>>,
		b: Array<Record<string, unknown>>,
	) => [...(a || []), ...(b || [])],
	generateMessageId: () => "test-id",
}));

import { ChatWindow } from "./ChatWindow";

beforeEach(() => {
	messagesRef.data = [];
	messagesRef.isLoading = false;
	streamRef.sendMessage = vi.fn();
	streamRef.isStreaming = false;
	streamRef.pendingQuestion = null;
	streamRef.stopStreaming = vi.fn();
	createConversationRef.mutateAsync = vi.fn();
	createConversationRef.isPending = false;
	storeSelectors.setActiveConversation.mockReset();
	storeSelectors.setActiveAgent.mockReset();
	storeSelectors.messagesByConversation = {};
	storeSelectors.systemEventsByConversation = {};
	storeSelectors.streamingMessageIds = {};
	storeSelectors.todos = [];
	mockNavigate.mockReset();
});

// --- tests --------------------------------------------------------------

describe("ChatWindow — empty states", () => {
	it("shows 'Start a conversation' CTA when no conversationId is set", () => {
		renderWithProviders(<ChatWindow conversationId={undefined} />);
		expect(
			screen.getByRole("heading", { name: /start a conversation/i }),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/chat input/i)).toBeInTheDocument();
	});

	it("shows the agent-specific greeting when an agent name is provided", () => {
		renderWithProviders(
			<ChatWindow conversationId="c-1" agentName="SupportBot" />,
		);
		expect(
			screen.getByRole("heading", { name: /chat with supportbot/i }),
		).toBeInTheDocument();
	});
});

describe("ChatWindow — loading state", () => {
	it("renders skeletons while messages are loading", () => {
		messagesRef.isLoading = true;
		const { container } = renderWithProviders(
			<ChatWindow conversationId="c-1" />,
		);
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
	});
});

describe("ChatWindow — messages render & send", () => {
	it("shows an immediate thinking activity line while the run is starting", () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "Build a report",
				created_at: "2026-04-20T00:00:00Z",
			},
		];
		streamRef.isStreaming = true;

		renderWithProviders(<ChatWindow conversationId="c-1" />);

		expect(screen.getByText("Thinking…")).toHaveClass(
			"chat-activity-shimmer",
		);
	});

	it("collapses tool activity when the final response starts streaming", () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "Build a report",
				created_at: "2026-04-20T00:00:00Z",
			},
			{
				id: "tool-1",
				role: "tool_call",
				tool_name: "create_text_artifact",
				tool_state: "completed",
				created_at: "2026-04-20T00:00:01Z",
			},
			{
				id: "assistant-final",
				role: "assistant",
				content: "I created the report.",
				isStreaming: true,
				created_at: "2026-04-20T00:00:02Z",
			},
		];
		streamRef.isStreaming = true;
		storeSelectors.streamingMessageIds = {
			"c-1": "assistant-final",
		};

		const { container } = renderWithProviders(
			<ChatWindow conversationId="c-1" />,
		);

		expect(screen.getByText("Responding…")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /Responding/i }),
		).toHaveAttribute("aria-expanded", "false");
		expect(container.querySelector(".grid-rows-\\[0fr\\]")).not.toBeNull();
	});

	it("renders messages returned from the hook", () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "ping",
				created_at: "2026-04-20T00:00:00Z",
			},
			{
				id: "m-2",
				role: "assistant",
				content: "pong",
				created_at: "2026-04-20T00:00:01Z",
			},
		];

		renderWithProviders(<ChatWindow conversationId="c-1" />);

		// Stubbed ChatMessage emits a data-marker for each message.
		expect(screen.getByText("ping")).toBeInTheDocument();
		expect(screen.getByText("pong")).toBeInTheDocument();
	});

	it("uses the persisted run summary duration even when its content is empty", () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "Generate files",
				created_at: "2026-04-20T00:00:00Z",
			},
			{
				id: "m-2",
				role: "assistant",
				content: "I created the files.",
				created_at: "2026-04-20T00:00:01Z",
			},
			{
				id: "m-3",
				role: "assistant",
				content: "",
				duration_ms: 8_500,
				created_at: "2026-04-20T00:00:09Z",
			},
		];

		renderWithProviders(<ChatWindow conversationId="c-1" />);

		expect(screen.getByText("Worked for 9s")).toBeInTheDocument();
	});

	it("does not mark agent-switch activity complete before the run summary arrives", () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "Check my tickets",
				created_at: "2026-04-20T00:00:00Z",
			},
		];
		storeSelectors.systemEventsByConversation = {
			"c-1": [
				{
					id: "event-1",
					type: "agent_switch",
					timestamp: "2026-04-20T00:00:01Z",
					agentName: "Work Tracking Agent",
					agentId: "agent-work",
					reason: "automatic",
				},
			],
		};
		streamRef.isStreaming = false;

		renderWithProviders(<ChatWindow conversationId="c-1" />);

		expect(screen.queryByText(/Worked for/i)).not.toBeInTheDocument();
	});

	it("forwards a typed message to the stream's sendMessage", async () => {
		messagesRef.data = [
			{
				id: "m-1",
				role: "user",
				content: "ping",
				created_at: "2026-04-20T00:00:00Z",
			},
		];

		renderWithProviders(<ChatWindow conversationId="c-1" />);

		const input = screen.getByLabelText(/chat input/i) as HTMLInputElement;
		fireEvent.change(input, { target: { value: "hello" } });
		fireEvent.keyDown(input, { key: "Enter" });

		await waitFor(() =>
			expect(streamRef.sendMessage).toHaveBeenCalledWith(
				"hello",
				"c-1",
				[],
				"balanced",
			),
		);
	});

	it("creates a conversation from the blank draft and sends the first message", async () => {
		createConversationRef.mutateAsync.mockResolvedValue({
			id: "new-conversation-id",
			agent_id: null,
		});

		renderWithProviders(<ChatWindow conversationId={undefined} />);

		const input = screen.getByLabelText(/chat input/i) as HTMLInputElement;
		fireEvent.change(input, { target: { value: "hello from draft" } });
		fireEvent.keyDown(input, { key: "Enter" });

		await waitFor(() =>
			expect(createConversationRef.mutateAsync).toHaveBeenCalledWith({
				body: { channel: "chat" },
			}),
		);
		expect(storeSelectors.setActiveConversation).toHaveBeenCalledWith(
			"new-conversation-id",
		);
		expect(storeSelectors.setActiveAgent).toHaveBeenCalledWith(null);
		expect(mockNavigate).toHaveBeenCalledWith("/chat/new-conversation-id");
		expect(streamRef.sendMessage).toHaveBeenCalledWith(
			"hello from draft",
			"new-conversation-id",
			[],
			"balanced",
		);
	});
});
