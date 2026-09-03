/**
 * Durable chat runtime hook.
 *
 * HTTP starts and cancels server-owned runs. WebSocket carries only replayable
 * realtime events; reconnects hydrate the same projection from server state.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
	deriveActiveRun,
	type ChatProjectionSnapshot,
	type ChatStreamEnvelope,
} from "@/lib/chat-runtime";
import { generateMessageId } from "@/lib/chat-utils";
import {
	cancelChatRun,
	createChatRun,
	getChatRunState,
	type ChatRunStateResponse,
} from "@/services/chatRuns";
import type { AttachmentPublic } from "@/services/chatAttachments";
import type { ChatModelProfileId } from "@/services/chatModels";
import {
	webSocketService,
	type ChatAgentSwitch,
	type ChatStreamChunk,
} from "@/services/websocket";
import { useChatStore } from "@/stores/chatStore";

const STREAM_FLUSH_INTERVAL_MS = 32;

export interface UseChatStreamOptions {
	conversationId: string | undefined;
	onError?: (error: string) => void;
	onAgentSwitch?: (agentSwitch: ChatAgentSwitch) => void;
}

export interface UseChatStreamReturn {
	sendMessage: (
		message: string,
		conversationIdOverride?: string,
		attachments?: AttachmentPublic[],
		modelProfileId?: ChatModelProfileId | null,
	) => Promise<void>;
	isConnected: boolean;
	isStreaming: boolean;
	stopStreaming: () => Promise<void>;
}

function getChunk(input: ChatStreamEnvelope): ChatStreamChunk | null {
	return input.payload ?? input.chunk ?? null;
}

function isTerminalPlatformJob(status: string | undefined): boolean {
	return (
		status === "succeeded" || status === "failed" || status === "cancelled"
	);
}

function stateToProjectionSnapshot(
	state: ChatRunStateResponse,
): ChatProjectionSnapshot {
	const activeRun = state.active_run;
	const activeRunId = activeRun?.id ?? null;
	const activeRunConversationId = activeRun?.conversation_id ?? null;
	return {
		conversation_id: state.conversation.id,
		messages: state.messages ?? [],
		runs:
			activeRun && activeRunId
				? {
						[activeRunId]: {
							run_id: activeRunId,
							conversation_id: activeRunConversationId,
							status: activeRun.status,
							last_sequence: state.latest_sequence,
						},
					}
				: undefined,
		active_run_id: activeRunId,
		last_sequence: state.latest_sequence,
	};
}

export function useChatStream({
	conversationId,
	onError,
	onAgentSwitch,
}: UseChatStreamOptions): UseChatStreamReturn {
	const queryClient = useQueryClient();
	const [isConnected, setIsConnected] = useState(() =>
		webSocketService.isConnected(),
	);
	const chatUnsubscribeRef = useRef<(() => void) | null>(null);
	const subscribedConversationIdRef = useRef<string | null>(null);
	const currentConversationIdRef = useRef<string | undefined>(conversationId);
	const artifactJobUnsubscribersRef = useRef(new Map<string, () => void>());
	const queuedEventsRef = useRef<ChatStreamEnvelope[]>([]);
	const queuedConversationIdRef = useRef<string | null>(null);
	const flushTimerRef = useRef<number | null>(null);
	const onErrorRef = useRef(onError);
	const onAgentSwitchRef = useRef(onAgentSwitch);
	useEffect(() => {
		onErrorRef.current = onError;
		onAgentSwitchRef.current = onAgentSwitch;
	}, [onAgentSwitch, onError]);

	const projection = useChatStore((state) =>
		conversationId
			? state.projectionsByConversation[conversationId]
			: undefined,
	);
	const {
		applyChatRunEvent,
		applyChatRunEvents,
		hydrateConversationProjection,
		stageOptimisticUserTurn,
		setStreamError,
	} = useChatStore();

	const isStreaming = useMemo(
		() => Boolean(projection && deriveActiveRun(projection)),
		[projection],
	);
	const hasPendingSubmission = useMemo(
		() =>
			Boolean(
				projection && deriveActiveRun(projection)?.status === "pending",
			),
		[projection],
	);
	useEffect(() => {
		currentConversationIdRef.current = conversationId;
	}, [conversationId]);

	const flushQueuedEvents = useCallback(() => {
		flushTimerRef.current = null;
		const events = queuedEventsRef.current;
		queuedEventsRef.current = [];
		queuedConversationIdRef.current = null;
		if (events.length === 0) return;
		const targetConversationId = events[0]?.conversation_id;
		if (!targetConversationId) return;
		applyChatRunEvents(targetConversationId, events);
	}, [applyChatRunEvents]);

	const enqueueStreamEvent = useCallback(
		(event: ChatStreamEnvelope) => {
			const eventConversationId = event.conversation_id ?? null;
			if (
				queuedConversationIdRef.current &&
				eventConversationId &&
				queuedConversationIdRef.current !== eventConversationId
			) {
				if (flushTimerRef.current !== null) {
					window.clearTimeout(flushTimerRef.current);
				}
				flushQueuedEvents();
			}
			queuedConversationIdRef.current = eventConversationId;
			queuedEventsRef.current.push(event);
			if (flushTimerRef.current !== null) return;
			flushTimerRef.current = window.setTimeout(
				flushQueuedEvents,
				STREAM_FLUSH_INTERVAL_MS,
			);
		},
		[flushQueuedEvents],
	);

	useEffect(
		() => () => {
			artifactJobUnsubscribersRef.current.forEach((unsubscribe) =>
				unsubscribe(),
			);
			artifactJobUnsubscribersRef.current.clear();
			chatUnsubscribeRef.current?.();
			chatUnsubscribeRef.current = null;
			subscribedConversationIdRef.current = null;
			if (flushTimerRef.current !== null) {
				window.clearTimeout(flushTimerRef.current);
			}
			flushTimerRef.current = null;
			queuedEventsRef.current = [];
			queuedConversationIdRef.current = null;
		},
		[],
	);

	const hydrateRunState = useCallback(
		(targetConversationId: string, state: ChatRunStateResponse) => {
			hydrateConversationProjection(
				targetConversationId,
				stateToProjectionSnapshot(state),
				(state.events ?? []) as unknown as ChatStreamEnvelope[],
			);
		},
		[hydrateConversationProjection],
	);

	const invalidateConversation = useCallback(
		(targetConversationId: string) => {
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/chat/conversations/{conversation_id}/messages",
					{
						params: {
							path: { conversation_id: targetConversationId },
						},
					},
				],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/chat/conversations"],
			});
		},
		[queryClient],
	);

	const observeArtifactJob = useCallback(
		(result: unknown, targetConversationId: string) => {
			if (!result || typeof result !== "object") return;
			const job = result as {
				type?: string;
				kind?: string;
				job_id?: string;
			};
			if (
				job.type !== "platform_job" ||
				job.kind !== "video_generation" ||
				!job.job_id ||
				artifactJobUnsubscribersRef.current.has(job.job_id)
			) {
				return;
			}

			const unsubscribe = webSocketService.onPlatformJobUpdate(
				job.job_id,
				(update) => {
					if (!isTerminalPlatformJob(update.status)) return;
					invalidateConversation(targetConversationId);
					queryClient.invalidateQueries({
						queryKey: ["chat-artifacts"],
					});
					if (update.status === "succeeded") {
						toast.success("Video ready");
					} else {
						toast.error("Video generation did not finish", {
							description:
								update.error?.message ||
								"Open the notification for details.",
						});
					}
					artifactJobUnsubscribersRef.current.get(job.job_id!)?.();
					artifactJobUnsubscribersRef.current.delete(job.job_id!);
				},
			);
			artifactJobUnsubscribersRef.current.set(job.job_id, unsubscribe);
		},
		[invalidateConversation, queryClient],
	);

	const handleStreamEvent = useCallback(
		(envelope: ChatStreamEnvelope) => {
			const targetConversationId =
				envelope.conversation_id ?? currentConversationIdRef.current;
			if (!targetConversationId) return;
			if (targetConversationId !== currentConversationIdRef.current)
				return;

			envelope.conversation_id = targetConversationId;
			enqueueStreamEvent(envelope);
			const chunk = getChunk(envelope);
			if (!chunk) return;

			if (chunk.type === "title_update") {
				invalidateConversation(targetConversationId);
			}
			if (chunk.type === "artifact_ready") {
				invalidateConversation(targetConversationId);
				queryClient.invalidateQueries({ queryKey: ["chat-artifacts"] });
			}
			if (chunk.type === "artifact_failed") {
				toast.error("File generation failed", {
					description:
						chunk.content || "The artifact could not be created.",
				});
			}
			if (chunk.type === "tool_result") {
				observeArtifactJob(
					chunk.tool_result?.result,
					targetConversationId,
				);
			}
			if (chunk.type === "agent_switch" && chunk.agent_switch) {
				onAgentSwitchRef.current?.(chunk.agent_switch);
			}
			if (chunk.type === "error") {
				const message = chunk.error || "Unknown error occurred";
				setStreamError(message);
				onErrorRef.current?.(message);
			}
			if (chunk.type === "done") {
				invalidateConversation(targetConversationId);
			}
		},
		[
			enqueueStreamEvent,
			invalidateConversation,
			observeArtifactJob,
			queryClient,
			setStreamError,
		],
	);

	const subscribeToConversation = useCallback(
		(targetConversationId: string) => {
			currentConversationIdRef.current = targetConversationId;
			if (
				subscribedConversationIdRef.current === targetConversationId &&
				chatUnsubscribeRef.current
			) {
				return;
			}

			chatUnsubscribeRef.current?.();
			chatUnsubscribeRef.current = webSocketService.onChatStream(
				targetConversationId,
				handleStreamEvent,
			);
			subscribedConversationIdRef.current = targetConversationId;
		},
		[handleStreamEvent],
	);

	useEffect(() => {
		if (!conversationId) return;
		let cancelled = false;
		subscribeToConversation(conversationId);
		// A client-generated first-turn conversation does not exist on the server
		// until its POST resolves. The send path connects and hydrates immediately
		// afterward; probing sooner only produces a 404 and a denied subscription.
		if (hasPendingSubmission) {
			return () => {
				cancelled = true;
			};
		}

		const setup = async () => {
			try {
				await webSocketService.connectToChat(conversationId);
				if (cancelled) return;
				setIsConnected(true);
			} catch (error) {
				if (cancelled) return;
				console.error("[useChatStream] Failed to connect:", error);
				setIsConnected(false);
				return;
			}

			try {
				const state = await getChatRunState(conversationId);
				if (!cancelled) hydrateRunState(conversationId, state);
			} catch (error) {
				if (!cancelled) {
					console.error(
						"[useChatStream] Failed to restore chat state:",
						error,
					);
				}
			}
		};
		void setup();
		return () => {
			cancelled = true;
		};
	}, [
		conversationId,
		hasPendingSubmission,
		hydrateRunState,
		subscribeToConversation,
	]);

	useEffect(() => {
		return webSocketService.onConnectionStatusChange((connected) => {
			setIsConnected(connected);
			const targetConversationId = currentConversationIdRef.current;
			if (!connected || !targetConversationId) return;
			const currentProjection =
				useChatStore.getState().projectionsByConversation[
					targetConversationId
				];
			if (
				currentProjection &&
				deriveActiveRun(currentProjection)?.status === "pending"
			)
				return;
			void getChatRunState(targetConversationId)
				.then((state) => hydrateRunState(targetConversationId, state))
				.catch((error) =>
					console.error(
						"[useChatStream] Failed to replay chat state:",
						error,
					),
				);
		});
	}, [hydrateRunState]);

	const sendMessage = useCallback(
		async (
			message: string,
			conversationIdOverride?: string,
			attachments: AttachmentPublic[] = [],
			modelProfileId: ChatModelProfileId | null = null,
		) => {
			const targetConversationId =
				conversationIdOverride ?? conversationId;
			if (!targetConversationId) {
				toast.error("No conversation selected");
				return;
			}

			const runId = generateMessageId();
			const userMessageId = generateMessageId();
			stageOptimisticUserTurn(targetConversationId, {
				conversation_id: targetConversationId,
				run_id: runId,
				user_message_id: userMessageId,
				local_id: userMessageId,
				content: message,
				attachments,
				model: modelProfileId,
				created_at: new Date().toISOString(),
			});
			subscribeToConversation(targetConversationId);

			try {
				await createChatRun({
					conversation_id: targetConversationId,
					content: message,
					client_run_id: runId,
					user_message_id: userMessageId,
					attachment_ids: attachments.map(
						(attachment) => attachment.id,
					),
					model_profile_id: modelProfileId,
				});
				// The command is durable before this resolves. Subscribe next, then
				// replay state to close the command/subscription race without making
				// the socket responsible for the run.
				await webSocketService.connectToChat(targetConversationId);
				const state = await getChatRunState(targetConversationId);
				hydrateRunState(targetConversationId, state);
				invalidateConversation(targetConversationId);
			} catch (error) {
				console.error(
					"[useChatStream] Failed to create chat run:",
					error,
				);
				const errorMessage =
					error instanceof Error
						? error.message
						: "Failed to send message";
				applyChatRunEvent(targetConversationId, {
					event_id: `local-error:${runId}`,
					conversation_id: targetConversationId,
					run_id: runId,
					kind: "error",
					status: "failed",
					payload: { type: "error", error: errorMessage },
				});
				setStreamError(errorMessage);
				throw error;
			}
		},
		[
			applyChatRunEvent,
			conversationId,
			hydrateRunState,
			invalidateConversation,
			setStreamError,
			stageOptimisticUserTurn,
			subscribeToConversation,
		],
	);

	const stopStreaming = useCallback(async () => {
		if (!conversationId || !projection) return;
		const activeRun = deriveActiveRun(projection);
		if (!activeRun) return;

		try {
			await cancelChatRun(activeRun.run_id);
			const state = await getChatRunState(conversationId);
			hydrateRunState(conversationId, state);
		} catch (error) {
			console.error("[useChatStream] Failed to cancel chat run:", error);
			const message =
				error instanceof Error
					? error.message
					: "Failed to stop message";
			setStreamError(message);
			onError?.(message);
		}
	}, [conversationId, hydrateRunState, onError, projection, setStreamError]);

	return {
		sendMessage,
		isConnected,
		isStreaming,
		stopStreaming,
	};
}
