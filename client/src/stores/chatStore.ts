/**
 * Chat Store - Zustand state management for chat conversations
 *
 * Manages:
 * - Active conversation and agent selection
 * - Messages for conversations (cached locally)
 * - Streaming state for real-time responses
 * - Studio mode for debugging tool calls
 */

import { create } from "zustand";
import type { components } from "@/lib/v1";

import type {
	ToolExecutionState,
	ToolExecutionStatus,
	ToolExecutionLog,
} from "@/components/chat/ToolExecutionCard";
import type { SystemEvent } from "@/components/chat/ChatSystemEvent";
import type { TodoItem } from "@/services/websocket";

// Use generated types from API
type AgentSummary = components["schemas"]["AgentSummary"];
type ConversationSummary = components["schemas"]["ConversationSummary"];
type MessagePublic = components["schemas"]["MessagePublic"];

// Re-export types for external use
export type { ToolExecutionState, ToolExecutionStatus, ToolExecutionLog };


/**
 * Persistent dedup state per conversation
 * Similar to Happy's ReducerState - tracks processed messages to prevent duplicates
 */
interface DedupState {
	processedIds: Set<string>; // All message IDs we've processed
	localIdToServerId: Map<string, string>; // localId -> server ID mapping
}

interface ChatState {
	// Active selections
	activeConversationId: string | null;
	activeAgentId: string | null;

	// Cached agents and conversations (for quick display)
	agents: AgentSummary[];
	conversations: ConversationSummary[];

	// Messages per conversation (cached locally)
	messagesByConversation: Record<string, MessagePublic[]>;

	// System events per conversation (agent switches, errors, etc.)
	systemEventsByConversation: Record<string, SystemEvent[]>;

	// Persisted tool executions per conversation (keyed by tool_call_id)
	// This allows us to show execution details after streaming completes
	toolExecutionsByConversation: Record<
		string,
		Record<string, ToolExecutionState>
	>;

	// Persistent dedup state per conversation (prevents duplicate messages)
	dedupStateByConversation: Record<string, DedupState>;

	// Streaming state
	isStreaming: boolean;

	// Studio mode (admin feature for debugging)
	isStudioMode: boolean;
	selectedToolCallId: string | null;

	// Connection state
	isConnected: boolean;
	error: string | null;

	// Todo list from agent tools (e.g., TodoWrite tool)
	todos: TodoItem[];

	/** Currently streaming message ID per conversation */
	streamingMessageIds: Record<string, string | null>;
}

interface ChatActions {
	// Selection actions
	setActiveConversation: (conversationId: string | null) => void;
	setActiveAgent: (agentId: string | null) => void;

	// Data actions
	setAgents: (agents: AgentSummary[]) => void;
	setConversations: (conversations: ConversationSummary[]) => void;
	addConversation: (conversation: ConversationSummary) => void;
	removeConversation: (conversationId: string) => void;
	updateConversation: (conversation: ConversationSummary) => void;

	// Message actions
	setMessages: (conversationId: string, messages: MessagePublic[]) => void;
	addMessage: (conversationId: string, message: MessagePublic) => void;
	updateMessage: (
		conversationId: string,
		messageId: string,
		updates: Partial<MessagePublic> & {
			isStreaming?: boolean;
			isFinal?: boolean;
			toolExecutions?: Record<string, ToolExecutionState>;
		},
	) => void;
	clearMessages: (conversationId: string) => void;

	// System event actions
	addSystemEvent: (conversationId: string, event: SystemEvent) => void;
	clearSystemEvents: (conversationId: string) => void;

	// Tool execution persistence actions
	saveToolExecutions: (
		conversationId: string,
		toolExecutions: Record<string, ToolExecutionState>,
	) => void;
	getToolExecution: (
		conversationId: string,
		toolCallId: string,
	) => ToolExecutionState | undefined;

	// Streaming actions
	startStreaming: () => void;
	completeStream: () => void;
	setStreamError: (error: string) => void;
	resetStream: () => void;

	// Studio mode actions
	toggleStudioMode: () => void;
	setStudioMode: (enabled: boolean) => void;
	selectToolCall: (toolCallId: string | null) => void;

	// Connection actions
	setConnected: (connected: boolean) => void;
	setError: (error: string | null) => void;

	// Todo list actions
	setTodos: (todos: TodoItem[]) => void;
	clearTodos: () => void;

	// Streaming message ID per conversation
	setStreamingMessageIdForConversation: (
		conversationId: string,
		messageId: string | null,
	) => void;

	// Dedup state actions
	markMessageProcessed: (conversationId: string, messageId: string) => void;
	mapLocalIdToServerId: (
		conversationId: string,
		localId: string,
		serverId: string,
	) => void;
	isMessageProcessed: (conversationId: string, messageId: string) => boolean;
	getServerIdForLocalId: (
		conversationId: string,
		localId: string,
	) => string | undefined;

	// Reset
	reset: () => void;
}

type ChatStore = ChatState & ChatActions;

const initialState: ChatState = {
	activeConversationId: null,
	activeAgentId: null,
	agents: [],
	conversations: [],
	systemEventsByConversation: {},
	toolExecutionsByConversation: {},
	dedupStateByConversation: {},
	messagesByConversation: {},
	isStreaming: false,
	isStudioMode: false,
	selectedToolCallId: null,
	isConnected: false,
	error: null,
	todos: [],
	streamingMessageIds: {},
};

export const useChatStore = create<ChatStore>((set, get) => ({
	...initialState,

	// Selection actions
	setActiveConversation: (conversationId) => {
		const previousConversationId = get().activeConversationId;
		set({ activeConversationId: conversationId });

		// Route synchronization can select the conversation that was just
		// created after its first message has already started streaming. Only a
		// genuine switch should clear the conversation-scoped activity state.
		if (
			previousConversationId !== conversationId &&
			get().isStreaming
		) {
			set({
				isStreaming: false,
			});
		}
	},

	setActiveAgent: (agentId) => {
		set({ activeAgentId: agentId });
	},

	// Data actions
	setAgents: (agents) => {
		set({ agents });
	},

	setConversations: (conversations) => {
		set({ conversations });
	},

	addConversation: (conversation) => {
		set((state) => ({
			conversations: [conversation, ...state.conversations],
		}));
	},

	removeConversation: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remainingMessages } =
				state.messagesByConversation;
			const { [conversationId]: _events, ...remainingEvents } =
				state.systemEventsByConversation;
			const { [conversationId]: _tools, ...remainingTools } =
				state.toolExecutionsByConversation;
			const { [conversationId]: _dedup, ...remainingDedup } =
				state.dedupStateByConversation;
			const { [conversationId]: _streaming, ...remainingStreamingIds } =
				state.streamingMessageIds;
			const wasActive =
				state.activeConversationId === conversationId;
			return {
				conversations: state.conversations.filter(
					(c) => c.id !== conversationId,
				),
				messagesByConversation: remainingMessages,
				systemEventsByConversation: remainingEvents,
				toolExecutionsByConversation: remainingTools,
				dedupStateByConversation: remainingDedup,
				streamingMessageIds: remainingStreamingIds,
				// Clear active if this was selected
				activeConversationId: wasActive
					? null
					: state.activeConversationId,
				// Reset streaming and todos if this was the active conversation
				...(wasActive && {
					isStreaming: false,
					todos: [],
				}),
			};
		});
	},

	updateConversation: (conversation) => {
		set((state) => ({
			conversations: state.conversations.map((c) =>
				c.id === conversation.id ? conversation : c,
			),
		}));
	},

	// Message actions
	setMessages: (conversationId, messages) => {
		set((state) => {
			// Deduplicate by ID - keep first occurrence
			const seen = new Set<string>();
			const deduped = messages.filter((m) => {
				if (seen.has(m.id)) return false;
				seen.add(m.id);
				return true;
			});

			// Preserve existing localIdToServerId mappings
			const existingDedup = state.dedupStateByConversation[conversationId];

			return {
				messagesByConversation: {
					...state.messagesByConversation,
					[conversationId]: deduped,
				},
				dedupStateByConversation: {
					...state.dedupStateByConversation,
					[conversationId]: {
						processedIds: seen,
						localIdToServerId:
							existingDedup?.localIdToServerId || new Map(),
					},
				},
			};
		});
	},

	addMessage: (conversationId, message) => {
		set((state) => {
			const existing = state.messagesByConversation[conversationId] || [];
			const dedupState = state.dedupStateByConversation[conversationId];

			// Check if already processed by ID
			if (dedupState?.processedIds.has(message.id)) {
				return {}; // Skip - already have this message
			}

			// Check if message already exists in array (fallback check)
			if (existing.some((m) => m.id === message.id)) {
				return {}; // Skip - already exists
			}

			// Add message and mark as processed
			const newProcessedIds = new Set(dedupState?.processedIds || []);
			newProcessedIds.add(message.id);

			return {
				messagesByConversation: {
					...state.messagesByConversation,
					[conversationId]: [...existing, message],
				},
				dedupStateByConversation: {
					...state.dedupStateByConversation,
					[conversationId]: {
						processedIds: newProcessedIds,
						localIdToServerId:
							dedupState?.localIdToServerId || new Map(),
					},
				},
			};
		});
	},

	updateMessage: (conversationId, messageId, updates) => {
		set((state) => {
			const messages = state.messagesByConversation[conversationId] || [];
			const index = messages.findIndex((m) => m.id === messageId);

			if (index === -1) return {};

			const updatedMessages = [...messages];
			updatedMessages[index] = {
				...updatedMessages[index],
				...updates,
			};

			return {
				messagesByConversation: {
					...state.messagesByConversation,
					[conversationId]: updatedMessages,
				},
			};
		});
	},

	clearMessages: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remaining } =
				state.messagesByConversation;
			return { messagesByConversation: remaining };
		});
	},

	// System event actions
	addSystemEvent: (conversationId, event) => {
		set((state) => ({
			systemEventsByConversation: {
				...state.systemEventsByConversation,
				[conversationId]: [
					...(state.systemEventsByConversation[conversationId] || []),
					event,
				],
			},
		}));
	},

	clearSystemEvents: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remaining } =
				state.systemEventsByConversation;
			return { systemEventsByConversation: remaining };
		});
	},

	// Tool execution persistence actions
	saveToolExecutions: (conversationId, toolExecutions) => {
		set((state) => ({
			toolExecutionsByConversation: {
				...state.toolExecutionsByConversation,
				[conversationId]: {
					...(state.toolExecutionsByConversation[conversationId] ||
						{}),
					...toolExecutions,
				},
			},
		}));
	},

	getToolExecution: (conversationId, toolCallId) => {
		return get().toolExecutionsByConversation[conversationId]?.[toolCallId];
	},

	// Streaming actions
	startStreaming: () => {
		set({ isStreaming: true });
	},

	completeStream: () => {
		set({ isStreaming: false });
	},

	setStreamError: (_error) => {
		set({ isStreaming: false });
	},

	resetStream: () => {
		set({ isStreaming: false });
	},

	// Studio mode actions
	toggleStudioMode: () => {
		set((state) => ({ isStudioMode: !state.isStudioMode }));
	},

	setStudioMode: (enabled) => {
		set({ isStudioMode: enabled });
	},

	selectToolCall: (toolCallId) => {
		set({ selectedToolCallId: toolCallId });
	},

	// Connection actions
	setConnected: (connected) => {
		set({ isConnected: connected });
	},

	setError: (error) => {
		set({ error });
	},

	// Todo list actions
	setTodos: (todos) => {
		set({ todos });
	},

	clearTodos: () => {
		set({ todos: [] });
	},

	// Streaming message ID per conversation
	setStreamingMessageIdForConversation: (conversationId, messageId) => {
		set((state) => ({
			streamingMessageIds: {
				...state.streamingMessageIds,
				[conversationId]: messageId,
			},
		}));
	},

	// Dedup state actions
	markMessageProcessed: (conversationId, messageId) => {
		set((state) => {
			const existing = state.dedupStateByConversation[conversationId];
			const newProcessedIds = new Set(existing?.processedIds || []);
			newProcessedIds.add(messageId);

			return {
				dedupStateByConversation: {
					...state.dedupStateByConversation,
					[conversationId]: {
						processedIds: newProcessedIds,
						localIdToServerId:
							existing?.localIdToServerId || new Map(),
					},
				},
			};
		});
	},

	mapLocalIdToServerId: (conversationId, localId, serverId) => {
		set((state) => {
			const existing = state.dedupStateByConversation[conversationId];
			const newLocalIdToServerId = new Map(
				existing?.localIdToServerId || [],
			);
			newLocalIdToServerId.set(localId, serverId);

			return {
				dedupStateByConversation: {
					...state.dedupStateByConversation,
					[conversationId]: {
						processedIds: existing?.processedIds || new Set(),
						localIdToServerId: newLocalIdToServerId,
					},
				},
			};
		});
	},

	isMessageProcessed: (conversationId, messageId) => {
		const dedupState = get().dedupStateByConversation[conversationId];
		return dedupState?.processedIds.has(messageId) ?? false;
	},

	getServerIdForLocalId: (conversationId, localId) => {
		const dedupState = get().dedupStateByConversation[conversationId];
		return dedupState?.localIdToServerId.get(localId);
	},

	// Reset
	reset: () => {
		set(initialState);
	},
}));

// Selector hooks for performance
export const useActiveConversation = () =>
	useChatStore((state) => state.activeConversationId);
export const useActiveAgent = () =>
	useChatStore((state) => state.activeAgentId);
export const useIsStreaming = () => useChatStore((state) => state.isStreaming);
export const useIsStudioMode = () =>
	useChatStore((state) => state.isStudioMode);
export const useTodos = () => useChatStore((state) => state.todos);
