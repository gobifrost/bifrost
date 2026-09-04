import type { components } from "@/lib/v1";
import type { ChatStreamChunk, ChatToolProgress } from "@/services/websocket";

type MessagePublic = components["schemas"]["MessagePublic"];

const CHAT_FAILURE_MESSAGE =
	"We couldn't complete this response. Please try again.";
const CHAT_TIMEOUT_MESSAGE =
	"This response took too long to complete. Please try again.";

export interface ChatContextWarningPayload {
	current_tokens: number;
	max_tokens: number;
	action: string;
	message: string;
}

export interface ChatStreamEnvelope {
	protocol_version?: string | number | null;
	event_id?: string | null;
	sequence?: number | null;
	conversation_id?: string | null;
	run_id?: string | null;
	occurred_at?: string | null;
	kind?: string | null;
	status?: string | null;
	payload?: ChatStreamChunk | null;
	chunk?: ChatStreamChunk | null;
	[key: string]: unknown;
}

export interface ChatProjectionSnapshot {
	conversation_id?: string | null;
	messages?: Array<MessagePublic | ChatRuntimeMessage> | null;
	system_events?: ChatSystemEvent[] | null;
	runs?: Record<string, Partial<ChatRunProjection> | null> | null;
	active_run_id?: string | null;
	last_sequence?: number | null;
}

export interface StageOptimisticUserTurnInput {
	conversation_id?: string | null;
	run_id: string;
	user_message_id: string;
	content: string;
	created_at?: string;
	local_id?: string | null;
	attachments?: MessagePublic["attachments"];
	model?: string | null;
	sequence?: number | null;
}

export type ChatSystemEvent =
	| {
			type: "agent_switch";
			id: string;
			conversation_id: string | null;
			run_id: string | null;
			turn_id: string | null;
			timestamp: string;
			sequence: number | null;
			agent_id: string;
			agent_name: string;
			reason: string;
	  }
	| {
			type: "error";
			id: string;
			conversation_id: string | null;
			run_id: string | null;
			turn_id: string | null;
			timestamp: string;
			sequence: number | null;
			error: string;
	  }
	| {
			type: "context_warning";
			id: string;
			conversation_id: string | null;
			run_id: string | null;
			turn_id: string | null;
			timestamp: string;
			sequence: number | null;
			warning: ChatContextWarningPayload;
	  };

export interface ChatRuntimeMessage extends MessagePublic {
	local_id?: string | null;
	run_id?: string | null;
	is_optimistic?: boolean;
	is_streaming?: boolean;
	is_final?: boolean;
	tool_progress?: ChatToolProgress | null;
}

export interface ChatRunProjection {
	run_id: string;
	conversation_id: string | null;
	status: string | null;
	last_sequence: number;
	last_event_id: string | null;
	applied_event_ids: string[];
	user_message_id: string | null;
	assistant_message_id: string | null;
	streaming_message_id: string | null;
}

export interface ChatProjection {
	conversation_id: string | null;
	messages: ChatRuntimeMessage[];
	system_events: ChatSystemEvent[];
	runs: Record<string, ChatRunProjection>;
	run_order: string[];
	active_run_id: string | null;
	last_sequence: number;
}

const TERMINAL_RUN_STATUSES = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"error",
	"done",
	"completed",
	"complete",
]);

const NONTERMINAL_PLATFORM_JOB_STATUSES = new Set([
	"queued",
	"running",
	"waiting",
	"cancel_requested",
]);

const DEFAULT_CREATED_AT = "1970-01-01T00:00:00.000Z";

function cloneMessage(message: ChatRuntimeMessage): ChatRuntimeMessage {
	return {
		...message,
		attachments: message.attachments
			? [...message.attachments]
			: message.attachments,
		tool_calls: message.tool_calls
			? [...message.tool_calls]
			: message.tool_calls,
	};
}

function cloneRun(run: ChatRunProjection): ChatRunProjection {
	return {
		...run,
		applied_event_ids: [...run.applied_event_ids],
	};
}

function cloneProjection(projection: ChatProjection): ChatProjection {
	return {
		conversation_id: projection.conversation_id,
		messages: projection.messages.map(cloneMessage),
		system_events: projection.system_events.map((event) => ({ ...event })),
		runs: Object.fromEntries(
			Object.entries(projection.runs).map(([runId, run]) => [
				runId,
				cloneRun(run),
			]),
		),
		run_order: [...projection.run_order],
		active_run_id: projection.active_run_id,
		last_sequence: projection.last_sequence,
	};
}

function makeEmptyRun(
	runId: string,
	conversationId: string | null,
): ChatRunProjection {
	return {
		run_id: runId,
		conversation_id: conversationId,
		status: null,
		last_sequence: 0,
		last_event_id: null,
		applied_event_ids: [],
		user_message_id: null,
		assistant_message_id: null,
		streaming_message_id: null,
	};
}

function ensureRun(
	projection: ChatProjection,
	runId: string,
	conversationId: string | null,
): ChatRunProjection {
	const existing = projection.runs[runId];
	if (existing) {
		if (!existing.conversation_id && conversationId) {
			existing.conversation_id = conversationId;
		}
		return existing;
	}

	const nextRun = makeEmptyRun(runId, conversationId);
	projection.runs = { ...projection.runs, [runId]: nextRun };
	projection.run_order = [...projection.run_order, runId];
	return nextRun;
}

function normalizeStatus(status: string | null | undefined): string | null {
	if (status == null) return null;
	const normalized = status.trim().toLowerCase();
	return normalized.length > 0 ? normalized : null;
}

function isTerminalRunStatus(status: string | null | undefined): boolean {
	const normalized = normalizeStatus(status);
	return normalized ? TERMINAL_RUN_STATUSES.has(normalized) : false;
}

function isPlatformJobResult(
	result: unknown,
): result is Record<string, unknown> {
	if (!result || typeof result !== "object") return false;
	const record = result as Record<string, unknown>;
	return (
		record.type === "platform_job" ||
		record.kind === "platform_job" ||
		"job_id" in record ||
		"id" in record
	);
}

function isNonterminalPlatformJobResult(result: unknown): boolean {
	if (!isPlatformJobResult(result)) return false;
	const status = normalizeStatus(result.status as string | null | undefined);
	return status ? NONTERMINAL_PLATFORM_JOB_STATUSES.has(status) : false;
}

function makeSyntheticMessageId(
	runId: string,
	sequence: number | null,
	suffix: string,
): string {
	return `synthetic:${runId}:${sequence ?? "na"}:${suffix}`;
}

function makeSyntheticEventId(
	kind: string,
	runId: string | null,
	sequence: number | null,
): string {
	return `synthetic-event:${kind}:${runId ?? "global"}:${sequence ?? "na"}`;
}

function getChunk(input: ChatStreamEnvelope): ChatStreamChunk | null {
	return input.payload ?? input.chunk ?? null;
}

function getConversationId(
	projection: ChatProjection,
	inputConversationId: string | null | undefined,
): string | null {
	if (inputConversationId == null) return projection.conversation_id;
	if (projection.conversation_id == null) return inputConversationId;
	return projection.conversation_id === inputConversationId
		? projection.conversation_id
		: projection.conversation_id;
}

function shouldApplyToConversation(
	projection: ChatProjection,
	conversationId: string | null | undefined,
): boolean {
	return (
		conversationId == null ||
		projection.conversation_id == null ||
		projection.conversation_id === conversationId
	);
}

function findMessageIndexById(
	messages: ChatRuntimeMessage[],
	messageId: string,
): number {
	return messages.findIndex((message) => message.id === messageId);
}

function findMessageIndexByLocalId(
	messages: ChatRuntimeMessage[],
	localId: string,
): number {
	return messages.findIndex((message) => message.local_id === localId);
}

function findMessageIndexByToolCallId(
	messages: ChatRuntimeMessage[],
	toolCallId: string,
): number {
	return messages.findIndex((message) => message.tool_call_id === toolCallId);
}

function findLatestAssistantMessageIndex(
	projection: ChatProjection,
	runId: string,
): number {
	for (let index = projection.messages.length - 1; index >= 0; index -= 1) {
		const message = projection.messages[index];
		if (message.run_id === runId && message.role === "assistant") {
			return index;
		}
	}

	return -1;
}

function mergeMessage(
	existing: ChatRuntimeMessage,
	incoming: Partial<ChatRuntimeMessage>,
): ChatRuntimeMessage {
	return {
		...existing,
		...incoming,
		attachments: incoming.attachments ?? existing.attachments,
		tool_calls: incoming.tool_calls ?? existing.tool_calls,
		tool_progress:
			incoming.tool_progress !== undefined
				? incoming.tool_progress
				: existing.tool_progress,
		tool_result:
			incoming.tool_result !== undefined
				? incoming.tool_result
				: existing.tool_result,
		content:
			incoming.content !== undefined
				? incoming.content
				: existing.content,
		local_id:
			incoming.local_id !== undefined
				? incoming.local_id
				: existing.local_id,
		run_id:
			incoming.run_id !== undefined ? incoming.run_id : existing.run_id,
		is_optimistic:
			incoming.is_optimistic !== undefined
				? incoming.is_optimistic
				: existing.is_optimistic,
		is_streaming:
			incoming.is_streaming !== undefined
				? incoming.is_streaming
				: existing.is_streaming,
		is_final:
			incoming.is_final !== undefined
				? incoming.is_final
				: existing.is_final,
	};
}

function upsertMessage(
	projection: ChatProjection,
	message: ChatRuntimeMessage,
): void {
	const messages = projection.messages;
	const byId = findMessageIndexById(messages, message.id);
	const byLocalId =
		message.local_id != null
			? findMessageIndexByLocalId(messages, message.local_id)
			: -1;
	const index = byId >= 0 ? byId : byLocalId >= 0 ? byLocalId : -1;

	if (index >= 0) {
		messages[index] = mergeMessage(messages[index], message);
		return;
	}

	messages.push(message);
}

function replaceMessageAtIndex(
	projection: ChatProjection,
	index: number,
	message: ChatRuntimeMessage,
): void {
	if (index < 0 || index >= projection.messages.length) return;
	projection.messages[index] = mergeMessage(
		projection.messages[index],
		message,
	);
}

function removeMessageAtIndex(projection: ChatProjection, index: number): void {
	if (index < 0 || index >= projection.messages.length) return;
	projection.messages.splice(index, 1);
}

function addSystemEvent(
	projection: ChatProjection,
	event: ChatSystemEvent,
): void {
	const index = projection.system_events.findIndex(
		(existing) => existing.id === event.id,
	);
	if (index >= 0) {
		projection.system_events[index] = {
			...projection.system_events[index],
			...event,
		};
		return;
	}

	projection.system_events.push(event);
}

function updateRunStatus(
	run: ChatRunProjection,
	status: string | null | undefined,
): void {
	if (status == null) return;
	run.status = status;
}

function refreshActiveRun(projection: ChatProjection): void {
	const current = projection.active_run_id
		? projection.runs[projection.active_run_id]
		: null;
	if (
		current &&
		(!isTerminalRunStatus(current.status) || current.streaming_message_id)
	) {
		return;
	}

	const runs = Object.values(projection.runs);
	const nonterminal = runs.filter(
		(run) =>
			!isTerminalRunStatus(run.status) ||
			run.streaming_message_id != null,
	);
	if (nonterminal.length === 0) {
		projection.active_run_id = null;
		return;
	}

	nonterminal.sort((left, right) => {
		const leftStreaming = left.streaming_message_id ? 1 : 0;
		const rightStreaming = right.streaming_message_id ? 1 : 0;
		if (leftStreaming !== rightStreaming) {
			return rightStreaming - leftStreaming;
		}
		if (left.last_sequence !== right.last_sequence) {
			return right.last_sequence - left.last_sequence;
		}
		return left.run_id.localeCompare(right.run_id);
	});

	projection.active_run_id = nonterminal[0]?.run_id ?? null;
}

function applyRuntimeConversationGuard(
	projection: ChatProjection,
	conversationId: string | null | undefined,
): boolean {
	return shouldApplyToConversation(projection, conversationId);
}

function recordRunSequence(
	run: ChatRunProjection,
	sequence: number,
	eventId: string | null,
): void {
	if (sequence > run.last_sequence) {
		run.last_sequence = sequence;
	}
	if (eventId && !run.applied_event_ids.includes(eventId)) {
		run.applied_event_ids = [...run.applied_event_ids.slice(-255), eventId];
	}
}

function resolveStreamingAssistantMessageId(
	projection: ChatProjection,
	run: ChatRunProjection,
	sequence: number | null,
	chunk: ChatStreamChunk | null,
	eventId: string | null,
	createIfMissing: boolean,
): string | null {
	if (run.streaming_message_id) {
		return run.streaming_message_id;
	}

	const preferredId =
		chunk?.assistant_message_id ?? chunk?.message_id ?? null;
	if (preferredId) {
		const existing = findMessageIndexById(projection.messages, preferredId);
		if (existing >= 0) {
			return preferredId;
		}
	}

	if (!createIfMissing) return null;

	return (
		preferredId ??
		makeSyntheticMessageId(run.run_id, sequence, eventId ?? "assistant")
	);
}

function setRunStreamingMessage(
	run: ChatRunProjection,
	messageId: string | null,
): void {
	run.streaming_message_id = messageId;
	if (messageId) {
		run.assistant_message_id = messageId;
	}
}

function makeAuthoritativeMessage(
	message: MessagePublic | ChatRuntimeMessage,
	runtime: Partial<ChatRuntimeMessage> = {},
): ChatRuntimeMessage {
	return {
		...message,
		local_id:
			runtime.local_id ??
			(message as ChatRuntimeMessage).local_id ??
			null,
		run_id:
			runtime.run_id ?? (message as ChatRuntimeMessage).run_id ?? null,
		is_optimistic: runtime.is_optimistic ?? false,
		is_streaming: runtime.is_streaming ?? false,
		is_final: runtime.is_final ?? true,
		tool_progress:
			runtime.tool_progress ??
			(message as ChatRuntimeMessage).tool_progress ??
			null,
	};
}

function updateTopLevelLastSequence(
	projection: ChatProjection,
	sequence: number,
): void {
	if (sequence > projection.last_sequence) {
		projection.last_sequence = sequence;
	}
}

function finalizeAssistantMessage(
	projection: ChatProjection,
	run: ChatRunProjection,
	messageId: string | null,
	fullContent: string | null | undefined,
	sequence: number | null,
	durationMs: number | null | undefined,
	tokenCountInput: number | null | undefined,
	tokenCountOutput: number | null | undefined,
	isTerminal: boolean,
): void {
	let targetIndex = -1;

	if (messageId) {
		targetIndex = findMessageIndexById(projection.messages, messageId);
	}
	if (targetIndex < 0 && run.streaming_message_id) {
		targetIndex = findMessageIndexById(
			projection.messages,
			run.streaming_message_id,
		);
	}
	if (targetIndex < 0) {
		targetIndex = findLatestAssistantMessageIndex(projection, run.run_id);
	}

	if (targetIndex < 0 && fullContent != null) {
		const syntheticId =
			messageId ??
			makeSyntheticMessageId(run.run_id, sequence, "assistant-final");
		projection.messages.push({
			id: syntheticId,
			conversation_id:
				run.conversation_id ?? projection.conversation_id ?? "",
			role: "assistant",
			content: fullContent,
			sequence: sequence ?? projection.last_sequence,
			created_at: DEFAULT_CREATED_AT,
			run_id: run.run_id,
			is_optimistic: false,
			is_streaming: false,
			is_final: true,
			token_count_input: tokenCountInput ?? null,
			token_count_output: tokenCountOutput ?? null,
			duration_ms: durationMs ?? null,
		});
		run.assistant_message_id = syntheticId;
		run.streaming_message_id = null;
		return;
	}

	if (targetIndex < 0) return;

	const existing = projection.messages[targetIndex];
	const nextId = messageId ?? existing.id;
	const nextContent =
		fullContent !== undefined && fullContent !== null
			? fullContent
			: existing.content;

	projection.messages[targetIndex] = {
		...existing,
		id: nextId,
		content: nextContent,
		sequence: sequence ?? existing.sequence,
		is_streaming: false,
		is_final: isTerminal,
		token_count_input:
			tokenCountInput !== undefined
				? tokenCountInput
				: existing.token_count_input,
		token_count_output:
			tokenCountOutput !== undefined
				? tokenCountOutput
				: existing.token_count_output,
		duration_ms:
			durationMs !== undefined ? durationMs : existing.duration_ms,
		run_id: run.run_id,
	};

	run.assistant_message_id = nextId;
	run.streaming_message_id = null;
}

function applyMessageStart(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	occurredAt: string,
): void {
	if (chunk.user_message_id) {
		const localId = chunk.local_id ?? chunk.user_message_id;
		const authoritative = {
			id: chunk.user_message_id,
			conversation_id:
				run.conversation_id ?? projection.conversation_id ?? "",
			role: "user" as const,
			sequence,
			created_at: occurredAt,
			run_id: run.run_id,
			local_id: localId,
			is_optimistic: false,
			is_streaming: false,
			is_final: true,
		};

		const serverIndex = findMessageIndexById(
			projection.messages,
			chunk.user_message_id,
		);
		const localIndex = findMessageIndexByLocalId(
			projection.messages,
			localId,
		);

		if (serverIndex >= 0 && localIndex >= 0 && serverIndex !== localIndex) {
			replaceMessageAtIndex(projection, serverIndex, authoritative);
			removeMessageAtIndex(
				projection,
				localIndex < serverIndex ? localIndex : localIndex - 1,
			);
		} else if (localIndex >= 0) {
			replaceMessageAtIndex(projection, localIndex, authoritative);
		} else if (serverIndex >= 0) {
			replaceMessageAtIndex(projection, serverIndex, authoritative);
		} else {
			projection.messages.push(authoritative);
		}

		run.user_message_id = chunk.user_message_id;
	}

	if (chunk.assistant_message_id) {
		const assistantIndex = findMessageIndexById(
			projection.messages,
			chunk.assistant_message_id,
		);
		const assistantMessage: ChatRuntimeMessage = {
			id: chunk.assistant_message_id,
			conversation_id:
				run.conversation_id ?? projection.conversation_id ?? "",
			role: "assistant",
			content: "",
			sequence,
			created_at: occurredAt,
			run_id: run.run_id,
			is_optimistic: false,
			is_streaming: true,
			is_final: false,
		};

		if (assistantIndex >= 0) {
			replaceMessageAtIndex(projection, assistantIndex, assistantMessage);
		} else {
			projection.messages.push(assistantMessage);
		}

		setRunStreamingMessage(run, chunk.assistant_message_id);
	}

	updateRunStatus(run, "running");
}

function applyDelta(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	occurredAt: string,
	eventId: string | null,
): void {
	if (!chunk.content) return;

	const streamingId = resolveStreamingAssistantMessageId(
		projection,
		run,
		sequence,
		chunk,
		eventId,
		true,
	);

	if (!streamingId) return;

	const existingIndex = findMessageIndexById(
		projection.messages,
		streamingId,
	);
	if (existingIndex < 0) {
		projection.messages.push({
			id: streamingId,
			conversation_id:
				run.conversation_id ?? projection.conversation_id ?? "",
			role: "assistant",
			content: chunk.content,
			sequence,
			created_at: occurredAt,
			run_id: run.run_id,
			is_optimistic: false,
			is_streaming: true,
			is_final: false,
		});
		setRunStreamingMessage(run, streamingId);
		updateRunStatus(run, "running");
		return;
	}

	const existing = projection.messages[existingIndex];
	projection.messages[existingIndex] = {
		...existing,
		content: `${existing.content ?? ""}${chunk.content}`,
		sequence,
		is_streaming: true,
		is_final: false,
		run_id: run.run_id,
	};
	setRunStreamingMessage(run, streamingId);
	updateRunStatus(run, "running");
}

function applyToolCall(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	occurredAt: string,
	eventId: string | null,
): void {
	if (!chunk.tool_call) return;

	const messageId =
		chunk.message_id ??
		makeSyntheticMessageId(
			run.run_id,
			sequence,
			chunk.tool_call.id ?? eventId ?? "tool-call",
		);
	const existingIndex = findMessageIndexById(projection.messages, messageId);
	const toolCallMessage: ChatRuntimeMessage = {
		id: messageId,
		conversation_id:
			run.conversation_id ?? projection.conversation_id ?? "",
		role: "tool_call",
		content: null,
		tool_calls: null,
		tool_call_id: chunk.tool_call.id,
		tool_name: chunk.tool_call.name,
		tool_input: chunk.tool_call.arguments,
		tool_state: "running",
		execution_id: chunk.execution_id ?? null,
		sequence,
		created_at: occurredAt,
		run_id: run.run_id,
		is_optimistic: false,
		is_streaming: true,
		is_final: false,
	};

	if (existingIndex >= 0) {
		replaceMessageAtIndex(projection, existingIndex, toolCallMessage);
	} else {
		projection.messages.push(toolCallMessage);
	}

	updateRunStatus(run, "running");
}

function applyToolProgress(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
): void {
	if (!chunk.tool_progress) return;

	const index = findMessageIndexByToolCallId(
		projection.messages,
		chunk.tool_progress.tool_call_id,
	);
	if (index < 0) return;

	const existing = projection.messages[index];
	projection.messages[index] = {
		...existing,
		tool_progress: chunk.tool_progress,
		tool_state: existing.tool_state ?? "running",
		sequence: Math.max(existing.sequence, sequence),
		run_id: run.run_id,
	};
	updateRunStatus(run, "running");
}

function applyToolResult(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
): void {
	if (!chunk.tool_result) return;

	const messageIndex = chunk.message_id
		? findMessageIndexById(projection.messages, chunk.message_id)
		: findMessageIndexByToolCallId(
				projection.messages,
				chunk.tool_result.tool_call_id,
			);
	if (messageIndex < 0) return;

	const existing = projection.messages[messageIndex];
	const result = chunk.tool_result.result;
	const isPlatformJob = isPlatformJobResult(result);
	const keepRunning = isPlatformJob && isNonterminalPlatformJobResult(result);

	projection.messages[messageIndex] = {
		...existing,
		tool_state: keepRunning
			? "running"
			: chunk.tool_result.error
				? "error"
				: "completed",
		tool_result: chunk.tool_result.error
			? { error: chunk.tool_result.error }
			: result,
		duration_ms: chunk.tool_result.duration_ms ?? existing.duration_ms,
		sequence: Math.max(existing.sequence, sequence),
		run_id: run.run_id,
		is_streaming: keepRunning || existing.is_streaming,
		is_final: !keepRunning && !chunk.tool_result.error,
	};

	updateRunStatus(run, "running");
}

function applyArtifactReady(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
): void {
	if (!chunk.artifact || !chunk.message_id) return;

	const messageIndex = findMessageIndexById(
		projection.messages,
		chunk.message_id,
	);
	if (messageIndex < 0) return;

	const existing = projection.messages[messageIndex];
	const attachments = existing.attachments ?? [];
	const artifact = {
		id: chunk.artifact.id,
		filename: chunk.artifact.filename,
		content_type: chunk.artifact.content_type,
		size_bytes: chunk.artifact.size_bytes,
		kind: "artifact" as const,
	};
	projection.messages[messageIndex] = {
		...existing,
		attachments: attachments.some((item) => item.id === artifact.id)
			? attachments
			: [...attachments, artifact],
		sequence: Math.max(existing.sequence, sequence),
		run_id: run.run_id,
	};
	updateRunStatus(run, "running");
}

function applyAgentSwitch(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	occurredAt: string,
	eventId: string | null,
): void {
	if (!chunk.agent_switch) return;

	addSystemEvent(projection, {
		type: "agent_switch",
		id:
			eventId ??
			makeSyntheticEventId("agent_switch", run.run_id, sequence),
		conversation_id: projection.conversation_id,
		run_id: run.run_id,
		turn_id: run.user_message_id,
		timestamp: occurredAt,
		sequence,
		agent_id: chunk.agent_switch.agent_id,
		agent_name: chunk.agent_switch.agent_name,
		reason: chunk.agent_switch.reason,
	});
}

function applyContextWarning(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	occurredAt: string,
	eventId: string | null,
): void {
	const warning = (
		chunk as ChatStreamChunk & {
			context_warning?: ChatContextWarningPayload | null;
		}
	).context_warning;
	const resolvedWarning: ChatContextWarningPayload =
		warning ??
		(chunk.content
			? {
					current_tokens: 0,
					max_tokens: 0,
					action: "warning",
					message: chunk.content,
				}
			: {
					current_tokens: 0,
					max_tokens: 0,
					action: "warning",
					message: chunk.error ?? "Context warning",
				});

	addSystemEvent(projection, {
		type: "context_warning",
		id:
			eventId ??
			makeSyntheticEventId("context_warning", run.run_id, sequence),
		conversation_id: projection.conversation_id,
		run_id: run.run_id,
		turn_id: run.user_message_id,
		timestamp: occurredAt,
		sequence,
		warning: resolvedWarning,
	});
}

function applyError(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	status: string | null,
	sequence: number,
	occurredAt: string,
	eventId: string | null,
): void {
	const error =
		(chunk.run_status ?? status) === "timeout"
			? CHAT_TIMEOUT_MESSAGE
			: CHAT_FAILURE_MESSAGE;
	addSystemEvent(projection, {
		type: "error",
		id: eventId ?? makeSyntheticEventId("error", run.run_id, sequence),
		conversation_id: projection.conversation_id,
		run_id: run.run_id,
		turn_id: run.user_message_id,
		timestamp: occurredAt,
		sequence,
		error,
	});

	if (run.streaming_message_id) {
		const index = findMessageIndexById(
			projection.messages,
			run.streaming_message_id,
		);
		if (index >= 0) {
			projection.messages[index] = {
				...projection.messages[index],
				is_streaming: false,
				is_final: false,
				sequence: Math.max(
					projection.messages[index].sequence,
					sequence,
				),
			};
		}
	}

	run.status = "error";
	run.streaming_message_id = null;
}

function applyDone(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
	statusHint: string | null | undefined,
): void {
	const content = chunk.content ?? null;
	const envelopeStatus = normalizeStatus(statusHint);
	const status = envelopeStatus ?? (chunk.error ? "error" : "succeeded");
	const terminalStatus =
		status && isTerminalRunStatus(status) ? status : "succeeded";

	finalizeAssistantMessage(
		projection,
		run,
		chunk.message_id ??
			run.streaming_message_id ??
			run.assistant_message_id,
		content,
		sequence,
		chunk.duration_ms ?? null,
		chunk.token_count_input ?? null,
		chunk.token_count_output ?? null,
		terminalStatus !== "error" &&
			terminalStatus !== "failed" &&
			terminalStatus !== "cancelled",
	);

	run.status = terminalStatus;
}

function applyAssistantMessageEnd(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	sequence: number,
): void {
	finalizeAssistantMessage(
		projection,
		run,
		chunk.message_id ??
			run.streaming_message_id ??
			run.assistant_message_id,
		null,
		sequence,
		chunk.duration_ms ?? null,
		chunk.token_count_input ?? null,
		chunk.token_count_output ?? null,
		true,
	);
	updateRunStatus(run, "running");
}

function applyRunStatus(
	projection: ChatProjection,
	run: ChatRunProjection,
	chunk: ChatStreamChunk,
	statusHint: string | null | undefined,
	sequence: number,
): void {
	const status =
		normalizeStatus(statusHint) ??
		normalizeStatus(chunk.run_status) ??
		(chunk.type === "cancelled" ? "cancelled" : null);
	if (!status) return;

	run.status = status;
	if (isTerminalRunStatus(status) && run.streaming_message_id) {
		const index = findMessageIndexById(
			projection.messages,
			run.streaming_message_id,
		);
		if (index >= 0) {
			projection.messages[index] = {
				...projection.messages[index],
				is_streaming: false,
				is_final:
					status !== "failed" &&
					status !== "error" &&
					status !== "cancelled",
				sequence: Math.max(
					projection.messages[index].sequence,
					sequence,
				),
			};
		}
		run.streaming_message_id = null;
	}
}

function mergeSnapshotMessage(
	existing: ChatRuntimeMessage | undefined,
	incoming: MessagePublic | ChatRuntimeMessage,
): ChatRuntimeMessage {
	if (!existing) {
		return makeAuthoritativeMessage(incoming, {
			is_optimistic: false,
			is_streaming: false,
			is_final: true,
		});
	}

	return {
		...makeAuthoritativeMessage(incoming, {
			is_optimistic: false,
			is_streaming: false,
			is_final: true,
		}),
		local_id:
			existing.local_id ??
			(incoming as ChatRuntimeMessage).local_id ??
			null,
		run_id:
			existing.run_id ?? (incoming as ChatRuntimeMessage).run_id ?? null,
		tool_progress:
			(incoming as ChatRuntimeMessage).tool_progress ??
			existing.tool_progress ??
			null,
	};
}

export function makeEmptyChatProjection(
	conversationId: string | null = null,
): ChatProjection {
	return {
		conversation_id: conversationId,
		messages: [],
		system_events: [],
		runs: {},
		run_order: [],
		active_run_id: null,
		last_sequence: 0,
	};
}

export function stageOptimisticUserTurn(
	projection: ChatProjection,
	input: StageOptimisticUserTurnInput,
): ChatProjection {
	const next = cloneProjection(projection);
	if (!applyRuntimeConversationGuard(next, input.conversation_id)) {
		return projection;
	}

	const conversationId = getConversationId(next, input.conversation_id);
	if (conversationId !== next.conversation_id) {
		next.conversation_id = conversationId;
	}

	const run = ensureRun(next, input.run_id, conversationId);
	run.status = "pending";
	run.user_message_id = input.user_message_id;
	run.streaming_message_id = null;

	upsertMessage(next, {
		id: input.user_message_id,
		conversation_id: conversationId ?? "",
		role: "user",
		content: input.content,
		attachments: input.attachments,
		model: input.model ?? null,
		sequence: input.sequence ?? next.last_sequence,
		created_at: input.created_at ?? DEFAULT_CREATED_AT,
		local_id: input.local_id ?? input.user_message_id,
		run_id: input.run_id,
		is_optimistic: true,
		is_streaming: false,
		is_final: false,
	});

	next.active_run_id = input.run_id;
	return next;
}

export function hydrateChatProjection(
	projection: ChatProjection,
	snapshot: ChatProjectionSnapshot,
): ChatProjection {
	const next = cloneProjection(projection);
	if (!applyRuntimeConversationGuard(next, snapshot.conversation_id)) {
		return projection;
	}

	const conversationId = getConversationId(next, snapshot.conversation_id);
	if (conversationId !== next.conversation_id) {
		next.conversation_id = conversationId;
	}

	const snapshotMessages = snapshot.messages ?? [];
	const snapshotMessageIds = new Set<string>();

	for (const message of snapshotMessages) {
		snapshotMessageIds.add(message.id);
		const existingIndex = findMessageIndexById(next.messages, message.id);
		const runtime = mergeSnapshotMessage(
			existingIndex >= 0 ? next.messages[existingIndex] : undefined,
			message,
		);
		if (existingIndex >= 0) {
			next.messages[existingIndex] = runtime;
		} else {
			next.messages.push(runtime);
		}
	}

	const retainedLiveMessages = projection.messages.filter((message) => {
		if (snapshotMessageIds.has(message.id)) return false;
		if (message.is_optimistic || message.is_streaming) return true;
		const run = message.run_id
			? (next.runs[message.run_id] ?? projection.runs[message.run_id])
			: null;
		return run != null && !isTerminalRunStatus(run.status);
	});

	for (const message of retainedLiveMessages) {
		if (snapshotMessageIds.has(message.id)) continue;
		if (findMessageIndexById(next.messages, message.id) >= 0) continue;
		next.messages.push(cloneMessage(message));
	}

	if (snapshot.runs) {
		for (const runId of Object.keys(snapshot.runs).sort()) {
			const incomingRun = snapshot.runs[runId];
			if (!incomingRun) continue;
			const existing =
				next.runs[runId] ?? makeEmptyRun(runId, conversationId);
			const incomingSequence =
				incomingRun.last_sequence ?? snapshot.last_sequence ?? 0;
			const existingIsTerminal = isTerminalRunStatus(existing.status);
			const incomingIsTerminal = isTerminalRunStatus(incomingRun.status);
			const preserveExistingStatus =
				(existingIsTerminal && !incomingIsTerminal) ||
				(incomingSequence < existing.last_sequence &&
					!incomingIsTerminal);
			next.runs[runId] = {
				...existing,
				...incomingRun,
				run_id: runId,
				status: preserveExistingStatus
					? existing.status
					: (incomingRun.status ?? existing.status),
				conversation_id:
					incomingRun.conversation_id ??
					existing.conversation_id ??
					conversationId,
				last_sequence: Math.max(
					existing.last_sequence,
					incomingSequence,
				),
				last_event_id:
					incomingRun.last_event_id ?? existing.last_event_id,
				applied_event_ids:
					incomingRun.applied_event_ids ?? existing.applied_event_ids,
				user_message_id:
					incomingRun.user_message_id ?? existing.user_message_id,
				assistant_message_id:
					incomingRun.assistant_message_id ??
					existing.assistant_message_id,
				streaming_message_id:
					incomingRun.streaming_message_id ??
					existing.streaming_message_id,
			};
			if (!next.run_order.includes(runId)) {
				next.run_order.push(runId);
			}
		}
	}

	if (snapshot.system_events) {
		for (const event of snapshot.system_events) {
			addSystemEvent(next, { ...event });
		}
	}

	if (snapshot.last_sequence != null) {
		next.last_sequence = Math.max(
			next.last_sequence,
			snapshot.last_sequence,
		);
	}

	for (const message of next.messages) {
		next.last_sequence = Math.max(
			next.last_sequence,
			message.sequence ?? 0,
		);
	}
	for (const run of Object.values(next.runs)) {
		next.last_sequence = Math.max(
			next.last_sequence,
			run.last_sequence ?? 0,
		);
	}

	next.active_run_id =
		snapshot.active_run_id ?? deriveActiveRun(next)?.run_id ?? null;
	refreshActiveRun(next);
	return next;
}

function applyEnvelopeToProjection(
	projection: ChatProjection,
	input: ChatStreamEnvelope,
): boolean {
	const chunk = getChunk(input);
	const envelope = input;
	const conversationId =
		envelope.conversation_id ?? chunk?.conversation_id ?? null;
	if (!applyRuntimeConversationGuard(projection, conversationId)) {
		return false;
	}

	const resolvedConversationId = getConversationId(
		projection,
		conversationId,
	);
	if (resolvedConversationId !== projection.conversation_id) {
		projection.conversation_id = resolvedConversationId;
	}

	if (!chunk) return false;

	const runId =
		envelope.run_id ??
		projection.active_run_id ??
		chunk.message_id ??
		chunk.user_message_id ??
		chunk.assistant_message_id ??
		null;
	if (!runId) {
		return false;
	}

	const run = ensureRun(projection, runId, resolvedConversationId);
	const sequence =
		typeof envelope.sequence === "number"
			? envelope.sequence
			: run.last_sequence + 1;
	const eventId = envelope.event_id ?? null;
	const occurredAt = envelope.occurred_at ?? DEFAULT_CREATED_AT;
	const dedupeId = eventId ?? null;

	if (dedupeId && run.applied_event_ids.includes(dedupeId)) {
		return false;
	}
	if (sequence <= run.last_sequence) {
		return false;
	}

	recordRunSequence(run, sequence, dedupeId);
	updateTopLevelLastSequence(projection, sequence);

	switch (chunk.type) {
		case "message_start":
			applyMessageStart(projection, run, chunk, sequence, occurredAt);
			break;
		case "delta":
			applyDelta(projection, run, chunk, sequence, occurredAt, dedupeId);
			break;
		case "tool_call":
			applyToolCall(
				projection,
				run,
				chunk,
				sequence,
				occurredAt,
				dedupeId,
			);
			break;
		case "tool_progress":
			applyToolProgress(projection, run, chunk, sequence);
			break;
		case "tool_result":
			applyToolResult(projection, run, chunk, sequence);
			break;
		case "assistant_message_end":
			applyAssistantMessageEnd(projection, run, chunk, sequence);
			break;
		case "agent_switch":
			applyAgentSwitch(
				projection,
				run,
				chunk,
				sequence,
				occurredAt,
				dedupeId,
			);
			break;
		case "context_warning":
			applyContextWarning(
				projection,
				run,
				chunk,
				sequence,
				occurredAt,
				dedupeId,
			);
			break;
		case "run_status":
		case "cancelled":
			applyRunStatus(
				projection,
				run,
				chunk,
				envelope.status ?? null,
				sequence,
			);
			break;
		case "done":
			applyDone(
				projection,
				run,
				chunk,
				sequence,
				envelope.status ?? null,
			);
			break;
		case "error":
			applyError(
				projection,
				run,
				chunk,
				envelope.status ?? null,
				sequence,
				occurredAt,
				dedupeId,
			);
			break;
		case "artifact_ready":
			applyArtifactReady(projection, run, chunk, sequence);
			break;
		case "artifact_started":
		case "artifact_failed":
		case "title_update":
			break;
		default:
			break;
	}

	refreshActiveRun(projection);
	return true;
}

export function applyChatStreamEnvelope(
	projection: ChatProjection,
	input: ChatStreamEnvelope,
): ChatProjection {
	const next = cloneProjection(projection);
	return applyEnvelopeToProjection(next, input) ? next : projection;
}

/** Apply one render-frame's events with a single projection clone. */
export function applyChatStreamEnvelopes(
	projection: ChatProjection,
	inputs: ChatStreamEnvelope[],
): ChatProjection {
	if (inputs.length === 0) return projection;
	const next = cloneProjection(projection);
	let changed = false;
	for (const input of inputs) {
		changed = applyEnvelopeToProjection(next, input) || changed;
	}
	return changed ? next : projection;
}

export function deriveActiveRun(
	projection: ChatProjection,
): ChatRunProjection | null {
	if (projection.active_run_id) {
		const active = projection.runs[projection.active_run_id];
		if (
			active &&
			(!isTerminalRunStatus(active.status) || active.streaming_message_id)
		) {
			return active;
		}
	}

	const withStreaming = Object.values(projection.runs).filter(
		(run) => run.streaming_message_id != null,
	);
	if (withStreaming.length > 0) {
		withStreaming.sort((left, right) => {
			if (left.last_sequence !== right.last_sequence) {
				return right.last_sequence - left.last_sequence;
			}
			return left.run_id.localeCompare(right.run_id);
		});
		return withStreaming[0] ?? null;
	}

	const nonterminal = Object.values(projection.runs).filter(
		(run) => !isTerminalRunStatus(run.status),
	);
	if (nonterminal.length === 0) return null;
	nonterminal.sort((left, right) => {
		if (left.last_sequence !== right.last_sequence) {
			return right.last_sequence - left.last_sequence;
		}
		return left.run_id.localeCompare(right.run_id);
	});
	return nonterminal[0] ?? null;
}

export function getCurrentStreamingMessage(
	projection: ChatProjection,
): ChatRuntimeMessage | null {
	const activeRun = deriveActiveRun(projection);
	if (!activeRun) return null;

	if (activeRun.streaming_message_id) {
		const byId = projection.messages.find(
			(message) => message.id === activeRun.streaming_message_id,
		);
		if (byId) return byId;
	}

	return (
		projection.messages.find(
			(message) =>
				message.run_id === activeRun.run_id &&
				message.is_streaming === true,
		) ?? null
	);
}

export function getRunById(
	projection: ChatProjection,
	runId: string,
): ChatRunProjection | null {
	return projection.runs[runId] ?? null;
}
