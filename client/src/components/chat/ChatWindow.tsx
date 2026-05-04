/**
 * ChatWindow Component
 *
 * Main chat message display area with auto-scroll.
 * Shows messages for the active conversation.
 */

import { useEffect, useRef, useMemo, useState, useCallback } from "react";
import { Bot, MessageSquare } from "lucide-react";
import { ChatMessage } from "./ChatMessage";
import { ChatInput } from "./ChatInput";
import { ToolExecutionCard } from "./ToolExecutionCard";
import { ToolExecutionBadge } from "./ToolExecutionBadge";
import { ToolExecutionGroup } from "./ToolExecutionGroup";
import { ChatSystemEvent, type SystemEvent } from "./ChatSystemEvent";
import { AskUserQuestionCard } from "./AskUserQuestionCard";
import { NeedsReauthCard, extractNeedsReauth } from "./NeedsReauthCard";
import { TodoList } from "./TodoList";
import { useChatStore, useTodos } from "@/stores/chatStore";
import { useMessages } from "@/hooks/useChat";
import { useChatStream } from "@/hooks/useChatStream";
import { Skeleton } from "@/components/ui/skeleton";
import type { components } from "@/lib/v1";
import { integrateMessages, type UnifiedMessage } from "@/lib/chat-utils";

type MessagePublic = components["schemas"]["MessagePublic"];

// Stable empty array to prevent re-render loops in Zustand selectors
const EMPTY_MESSAGES: MessagePublic[] = [];
const EMPTY_EVENTS: SystemEvent[] = [];

/** Helper component to render a message with its tool execution cards */
interface MessageWithToolCardsProps {
	message: MessagePublic;
	/** Map of tool_call_id -> tool result message (for getting execution_id) */
	toolResultMessages: Map<string, MessagePublic>;
	/** Conversation ID for retrieving saved tool execution state */
	conversationId: string;
	isStreaming?: boolean;
}

function MessageWithToolCards({
	message,
	toolResultMessages,
	conversationId,
	isStreaming,
}: MessageWithToolCardsProps) {
	// Get saved tool executions for this conversation
	const getToolExecution = useChatStore((state) => state.getToolExecution);

	// Check if this message has tool calls
	const hasToolCalls = message.tool_calls && message.tool_calls.length > 0;
	if (!hasToolCalls) {
		return (
			<ChatMessage
				message={message}
				isStreaming={isStreaming}
			/>
		);
	}

	// Determine if these are SDK tools (no workflow execution) or workflow tools
	// SDK tools don't have execution_id that maps to workflow executions
	const toolsInfo = message.tool_calls!.map((tc) => {
		const resultMsg = toolResultMessages.get(tc.id);
		const executionId =
			(resultMsg as { execution_id?: string | null } | undefined)
				?.execution_id ?? undefined;
		const savedExecution = getToolExecution(conversationId, tc.id);

		// SDK tool if: no execution_id OR we have saved streaming state
		// (saved state means it came from streaming, not workflow execution)
		const isSDKTool = !executionId || !!savedExecution;

		return { tc, resultMsg, executionId, savedExecution, isSDKTool };
	});

	// Check if all tools are SDK tools (use compact badges) or mixed/workflow (use cards)
	const allSDKTools = toolsInfo.every((t) => t.isSDKTool);

	return (
		<div className="space-y-1">
			{/* SDK Tools - compact badges with vertical connecting line */}
			{allSDKTools ? (
				<>
					<ToolExecutionGroup>
						{toolsInfo.map(({ tc, savedExecution, resultMsg }) => (
							<ToolExecutionBadge
								key={tc.id}
								toolCall={tc}
								status={
									savedExecution?.status ??
									(resultMsg ? "success" : "pending")
								}
								result={savedExecution?.result}
								error={savedExecution?.error}
								durationMs={savedExecution?.durationMs}
								logs={savedExecution?.logs}
							/>
						))}
					</ToolExecutionGroup>
					{/*
					 * needs_reauth (mockup §9). Tool dispatcher returns a
					 * ``ToolResult`` envelope with ``error_type=needs_reauth``
					 * and ``metadata.connection_id`` when the caller has no
					 * personal credential and there is no service fallback.
					 * Render an inline reconnect prompt for each affected tool
					 * — the message-level retry is the user's existing chat
					 * input.
					 */}
					{toolsInfo.map(({ tc, savedExecution, resultMsg }) => {
						const sourceResult =
							savedExecution?.result ??
							(resultMsg as { tool_result?: unknown } | undefined)
								?.tool_result;
						const reauth = extractNeedsReauth(sourceResult);
						if (!reauth) return null;
						return (
							<NeedsReauthCard
								key={`reauth-${tc.id}`}
								metadata={reauth}
							/>
						);
					})}
				</>
			) : (
				/* Workflow Tools - full cards with vertical connecting line */
				<ToolExecutionGroup>
					<div className="space-y-2 w-full">
						{toolsInfo.map(
							({
								tc,
								executionId,
								savedExecution,
								resultMsg,
							}) => (
								<ToolExecutionCard
									key={tc.id}
									executionId={executionId}
									toolCall={tc}
									execution={savedExecution}
									hasResultMessage={!!resultMsg}
								/>
							),
						)}
					</div>
				</ToolExecutionGroup>
			)}

			{/* Message text content (if any) */}
			{message.content && message.content.trim().length > 0 && (
				<ChatMessage
					message={message}
					isStreaming={isStreaming && !hasToolCalls}
				/>
			)}
		</div>
	);
}

interface ChatWindowProps {
	conversationId: string | undefined;
	agentName?: string | null;
}

// Threshold in pixels - if within this distance from bottom, consider "at bottom"
const SCROLL_THRESHOLD = 100;

export function ChatWindow({
	conversationId,
	agentName,
}: ChatWindowProps) {
	const messagesEndRef = useRef<HTMLDivElement>(null);
	const containerRef = useRef<HTMLDivElement>(null);

	// Track if user is at bottom of scroll area (for smart auto-scroll)
	const [isAtBottom, setIsAtBottom] = useState(true);

	// Check if scrolled to bottom
	const checkIfAtBottom = useCallback(() => {
		const container = containerRef.current;
		if (!container) return true;
		const { scrollTop, scrollHeight, clientHeight } = container;
		return scrollHeight - scrollTop - clientHeight < SCROLL_THRESHOLD;
	}, []);

	// Handle scroll events to track user position
	const handleScroll = useCallback(() => {
		setIsAtBottom(checkIfAtBottom());
	}, [checkIfAtBottom]);

	// Get messages from API and local cache
	const { data: apiMessages, isLoading: isLoadingMessages } =
		useMessages(conversationId);
	const localMessages = useChatStore(
		(state) =>
			(conversationId && state.messagesByConversation[conversationId]) ||
			EMPTY_MESSAGES,
	);
	const systemEvents = useChatStore(
		(state) =>
			(conversationId &&
				state.systemEventsByConversation[conversationId]) ||
			EMPTY_EVENTS,
	);
	const streamingMessageId = useChatStore((state) =>
		conversationId ? state.streamingMessageIds[conversationId] : null,
	);
	const todos = useTodos();

	// Use WebSocket streaming
	const {
		sendMessage,
		isStreaming,
		pendingQuestion,
		answerQuestion,
		stopStreaming,
	} = useChatStream({
		conversationId,
		onError: (error) => {
			console.error("[ChatWindow] Stream error:", error);
		},
	});

	// Merge API and local messages using unified message model
	const messages = useMemo(() => {
		const apiMsgs = (apiMessages || []) as UnifiedMessage[];
		const localMsgs = localMessages as UnifiedMessage[];

		return integrateMessages(apiMsgs, localMsgs);
	}, [apiMessages, localMessages]);

	// Build a map of tool_call_id -> tool result message for reconstructing state
	const toolResultMessages = useMemo(() => {
		const map = new Map<string, MessagePublic>();
		for (const msg of messages) {
			// Tool result messages have tool_call_id set
			if (msg.tool_call_id) {
				map.set(msg.tool_call_id, msg);
			}
		}
		return map;
	}, [messages]);

	// Create a unified timeline of messages and system events
	type TimelineItem =
		| { type: "message"; data: MessagePublic; timestamp: string }
		| { type: "tool_group"; data: MessagePublic[]; timestamp: string }
		| { type: "event"; data: SystemEvent; timestamp: string };

	const timeline = useMemo<TimelineItem[]>(() => {
		const items: TimelineItem[] = [];
		let currentToolGroup: MessagePublic[] = [];

		const flushToolGroup = () => {
			if (currentToolGroup.length > 0) {
				items.push({
					type: "tool_group",
					data: [...currentToolGroup],
					timestamp: currentToolGroup[0].created_at,
				});
				currentToolGroup = [];
			}
		};

		for (const msg of messages) {
			// Skip role: "tool" messages - they're for API compatibility only
			if (msg.role === "tool") {
				continue;
			}

			if (msg.role === "tool_call") {
				currentToolGroup.push(msg);
			} else {
				flushToolGroup();
				items.push({
					type: "message",
					data: msg,
					timestamp: msg.created_at,
				});
			}
		}
		flushToolGroup();

		// Add system events
		for (const event of systemEvents) {
			items.push({
				type: "event",
				data: event,
				timestamp: event.timestamp,
			});
		}

		// Sort by timestamp
		items.sort(
			(a, b) =>
				new Date(a.timestamp).getTime() -
				new Date(b.timestamp).getTime(),
		);

		return items;
	}, [messages, systemEvents]);

	// Auto-scroll to bottom on new messages or events (only if user is at bottom)
	useEffect(() => {
		if (isAtBottom) {
			messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
		}
	}, [messages, systemEvents, pendingQuestion, isAtBottom]);

	// Handle send message
	const handleSendMessage = (message: string) => {
		sendMessage(message);
	};

	// Empty state
	if (!conversationId) {
		return (
			<div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-8">
				<MessageSquare className="h-12 w-12 mb-4 opacity-20" />
				<h3 className="text-lg font-medium mb-2">
					Start a conversation
				</h3>
				<p className="text-sm text-center max-w-sm">
					Click "New Chat" to start a conversation. I can help with
					questions, tasks, and more. If you need specialized
					capabilities, I'll find the right tools to help.
				</p>
			</div>
		);
	}

	// Loading state
	if (isLoadingMessages) {
		return (
			<div className="flex-1 flex flex-col">
				<div className="flex-1 p-4 space-y-4">
					{[1, 2, 3].map((i) => (
						<div key={i} className="flex gap-3">
							<Skeleton className="h-8 w-8 rounded-full" />
							<div className="space-y-2 flex-1">
								<Skeleton className="h-4 w-20" />
								<Skeleton className="h-16 w-3/4" />
							</div>
						</div>
					))}
				</div>
				<ChatInput onSend={handleSendMessage} disabled />
			</div>
		);
	}

	// Empty conversation - check if no messages to display (excluding tool results)
	const hasDisplayableMessages = messages.some((msg) => !msg.tool_call_id);
	if (!hasDisplayableMessages && systemEvents.length === 0) {
		return (
			<div className="flex-1 flex flex-col">
				<div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-8">
					<Bot className="h-12 w-12 mb-4 opacity-20" />
					<h3 className="text-lg font-medium mb-2">
						{agentName
							? `Chat with ${agentName}`
							: "Start a conversation"}
					</h3>
					<p className="text-sm text-center max-w-sm mb-6">
						Send a message to start the conversation. The AI
						assistant will respond to your questions and help with
						tasks.
					</p>
				</div>
				<ChatInput
					onSend={handleSendMessage}
					placeholder="Send a message..."
				/>
			</div>
		);
	}

	return (
		<div className="flex-1 flex flex-col h-full overflow-hidden">
			{/* Messages Area */}
			<div
				ref={containerRef}
				onScroll={handleScroll}
				className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-muted scrollbar-track-transparent"
			>
				<div className="max-w-4xl mx-auto pt-8">
					{/* Unified message and event rendering */}
					{timeline.map((item) => {
						if (item.type === "event") {
							return (
								<ChatSystemEvent
									key={item.data.id}
									event={item.data}
								/>
							);
						}

						if (item.type === "tool_group") {
							return (
								<div key={`tools-${item.data[0].id}`}>
									<ToolExecutionGroup>
										{item.data.map((tc) => (
											<ToolExecutionBadge
												key={tc.id}
												toolCall={{
													id: tc.tool_call_id || tc.id,
													name: tc.tool_name || "unknown",
													arguments: tc.tool_input || {},
												}}
												status={
													tc.tool_state === "completed"
														? "success"
														: tc.tool_state === "error"
															? "failed"
															: "pending"
												}
												result={tc.tool_result}
												error={
													tc.tool_state === "error"
														? (tc.tool_result as { error?: string })?.error
														: undefined
												}
												durationMs={tc.duration_ms || undefined}
											/>
										))}
									</ToolExecutionGroup>
									{/* Inline reconnect prompts for any needs_reauth result. */}
									{item.data.map((tc) => {
										const reauth = extractNeedsReauth(tc.tool_result);
										if (!reauth) return null;
										return (
											<NeedsReauthCard
												key={`reauth-${tc.id}`}
												metadata={reauth}
											/>
										);
									})}
								</div>
							);
						}

						const msg = item.data;

						// Render user/assistant messages normally
						return (
							<MessageWithToolCards
								key={msg.id}
								message={msg}
								toolResultMessages={toolResultMessages}
								conversationId={conversationId}
								isStreaming={
									(msg as UnifiedMessage).isStreaming ||
									msg.id === streamingMessageId
								}
							/>
						);
					})}

					{/* Todo List - persistent checklist from SDK */}
					{todos.length > 0 && (
						<TodoList todos={todos} className="my-4" />
					)}

					{/* AskUserQuestion Card - inline at end of stream */}
					{pendingQuestion && (
						<AskUserQuestionCard
							questions={pendingQuestion.questions}
							onSubmit={answerQuestion}
							onCancel={stopStreaming}
						/>
					)}

					<div ref={messagesEndRef} />
				</div>
			</div>

			{/* Input Area */}
			<ChatInput
				onSend={handleSendMessage}
				isLoading={isStreaming}
				onStop={stopStreaming}
				placeholder={
					agentName ? `Message ${agentName}...` : "Send a message..."
				}
			/>
		</div>
	);
}
