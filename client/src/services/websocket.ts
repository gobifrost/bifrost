/**
 * WebSocket Service for real-time execution updates
 *
 * Uses native WebSocket connection to FastAPI backend.
 * Replaces Azure Web PubSub with simpler native implementation.
 *
 * Provides connection management and event subscriptions for:
 * - Execution status updates (for execution details screen)
 * - Execution log messages
 * - User notifications
 */

import type { components } from "@/lib/v1";
import { refreshAccessToken } from "@/lib/api-client";
import { getActiveToken } from "@/lib/auth-token";
import { useNotificationStore } from "@/stores/notificationStore";
import type { Notification } from "@/stores/notificationStore";

// Wait reasons for pending executions
export type WaitReason = "queued" | "memory_pressure";

// File activity events from CLI push/watch
export interface FileActivityEvent {
	type: "file_push" | "watch_start" | "watch_stop";
	user_id: string;
	user_name: string;
	prefix: string;
	file_count: number;
	is_watch: boolean;
	timestamp: string;
}

// App Builder live update types
export interface AppDraftUpdate {
	type: "app_draft_update";
	appId: string;
	entityType: "page" | "component" | "app";
	entityId: string;
	pageId?: string;
	userId: string;
	userName: string;
	timestamp: string;
}

// One esbuild error/warning, with location info so the UI can link to the file.
export interface BundleMessage {
	text: string;
	file: string | null;
	line: number | null;
	column: number | null;
	line_text: string | null;
}

// Code engine file update (includes full content for real-time preview)
export interface AppCodeFileUpdate {
	type: "app_code_file_update";
	appId: string;
	action: "create" | "update" | "delete";
	path: string;
	source: string | null;
	compiled: string | null;
	// Present after a successful bundle rebuild. Signals clients to reload
	// the bundle entry (URL changes because the hash changes).
	bundle: {
		entry: string;
		css: string | null;
		duration_ms: number;
	} | null;
	// Present when the bundle rebuild failed. The UI shows this as a
	// dismissible banner over the still-rendered last-good bundle.
	error: {
		messages: BundleMessage[];
	} | null;
	userId: string;
	userName: string;
	timestamp: string;
}

export interface AppPublishedUpdate {
	type: "app_published";
	appId: string;
	newVersionId: string;
	userId: string;
	userName: string;
	timestamp: string;
}

// Frontend-specific WebSocket event types (wrappers around backend messages)
export interface ExecutionUpdate {
	executionId: string;
	status: string;
	isComplete: boolean;
	timestamp: string;
	result?: unknown;
	error?: string;
	duration_ms?: number;
	// Queue visibility fields
	queuePosition?: number;
	waitReason?: WaitReason;
	availableMemoryMb?: number;
	requiredMemoryMb?: number;
}

// ExecutionLog from backend (auto-generated from OpenAPI)
export type ExecutionLogMessage = components["schemas"]["ExecutionLogPublic"];

// Frontend wrapper with execution context
// Note: id and sequence are optional for WebSocket logs (not yet persisted)
// but required in API responses (persisted logs have database IDs)
export interface ExecutionLog {
	executionId: string;
	id?: number; // Only present for persisted logs from API
	timestamp: string;
	level: string;
	message: string;
	data?: Record<string, unknown> | null;
	sequence?: number; // Only present when sequencing is enabled
}

export interface NewExecution {
	execution_id: string;
	workflow_name: string;
	executed_by: string;
	executed_by_name: string;
	status: string;
	started_at: string;
	timestamp: string;
}

export interface HistoryUpdate {
	execution_id: string;
	workflow_name: string;
	status: string;
	executed_by: string;
	executed_by_name: string;
	org_id?: string;
	started_at: string;
	completed_at?: string;
	duration_ms?: number;
	timestamp: string;
}

export interface PackageLog {
	level: string;
	message: string;
}

export interface PackageComplete {
	status: "success" | "error";
	message: string;
}

export interface PackageProgress {
	action: "install" | "uninstall";
	line: string;
	total: number;
	installing: number;
	// FOLDED count: workers past the install step (installed + recycling +
	// recycled). recycling/recycled below are raw — don't sum installed with
	// them or you'll double-count.
	installed: number;
	recycling: number;
	recycled: number;
	failed: number;
	failures: { worker: string; package: string | null; error: string | null }[];
}

export interface LocalRunnerStateUpdate {
	file_path: string;
	workflows: Array<{
		name: string;
		description: string;
		parameters: Array<{
			name: string;
			type: string;
			label: string | null;
			required: boolean;
			default_value: unknown;
		}>;
	}>;
	selected_workflow: string | null;
	pending: boolean;
	execution_id: string | null;
}

// Event source update types for real-time event streaming
export interface EventSourceEvent {
	id: string;
	event_source_id: string;
	event_type: string | null;
	status: string;
	received_at: string | null;
	source_ip: string | null;
	success_count: number;
	failed_count: number;
	delivery_count: number;
}

export interface EventSourceUpdate {
	type: "event_created" | "event_updated";
	event: EventSourceEvent;
}

// Agent run update types for real-time streaming
export interface AgentRunUpdate {
	type: "agent_run_update";
	run_id: string;
	agent_id: string;
	agent_name: string;
	status: string;
	trigger_type: string;
	iterations_used: number;
	tokens_used: number;
	duration_ms: number | null;
	error: string | null;
	org_id: string | null;
	started_at: string | null;
	completed_at: string | null;
	summary_status: string | null;
	summary_error: string | null;
	asked: string | null;
	did: string | null;
	confidence: number | null;
	timestamp: string;
}

export interface SummaryBackfillUpdate {
	type: "summary_backfill_update";
	job_id: string;
	total: number;
	succeeded: number;
	failed: number;
	status: string;
	estimated_cost_usd: string;
	actual_cost_usd: string;
	timestamp: string;
}

export interface AgentRunStepUpdate {
	type: "agent_run_step";
	run_id: string;
	step: {
		id: string;
		run_id: string;
		step_number: number;
		type: string;
		content: Record<string, unknown> | null;
		tokens_used: number | null;
		duration_ms: number | null;
	};
	timestamp: string;
}

// Chat streaming types
export interface ChatToolCall {
	id: string;
	name: string;
	arguments: Record<string, unknown>;
}

export interface ChatToolResult {
	tool_call_id: string;
	tool_name: string;
	result: unknown;
	error?: string | null;
	duration_ms?: number | null;
}

export interface ChatAgentSwitch {
	agent_id: string;
	agent_name: string;
	reason: string;
}

export interface ChatToolProgress {
	tool_call_id: string;
	execution_id?: string;
	status?: "pending" | "running" | "success" | "failed" | "timeout";
	log?: {
		level: "debug" | "info" | "warning" | "error";
		message: string;
	};
}

// AskUserQuestion types for SDK permission prompts
export interface AskUserQuestionOption {
	label: string;
	description: string;
}

export interface AskUserQuestion {
	question: string;
	header: string;
	options: AskUserQuestionOption[];
	multi_select: boolean;
}

// TodoItem type for todo list updates from SDK
export interface TodoItem {
	content: string;
	status: "pending" | "in_progress" | "completed";
	active_form: string;
}

export interface ChatStreamChunk {
	type:
		| "message_start"
		| "delta"
		| "tool_call"
		| "tool_progress"
		| "tool_result"
		| "agent_switch"
		| "done"
		| "error"
		| "title_update"
		| "ask_user_question"
		| "assistant_message_start"
		| "assistant_message_end"
		| "todo_update"
		| "context_warning";
	conversation_id?: string;
	content?: string | null;
	tool_call?: ChatToolCall | null;
	tool_progress?: ChatToolProgress | null;
	tool_result?: ChatToolResult | null;
	agent_switch?: ChatAgentSwitch | null;
	message_id?: string | null;
	// message_start fields - real UUIDs sent before streaming begins
	user_message_id?: string | null;
	assistant_message_id?: string | null;
	token_count_input?: number | null;
	token_count_output?: number | null;
	duration_ms?: number | null;
	error?: string | null;
	execution_id?: string | null;
	title?: string | null;
	// AskUserQuestion fields
	questions?: AskUserQuestion[] | null;
	request_id?: string | null;
	// Message boundary fields (for assistant_message_end)
	stop_reason?: "tool_use" | "end_turn" | null;
	// Todo list fields (for todo_update)
	todos?: TodoItem[] | null;
	// Client-generated ID echoed back for dedup
	local_id?: string | null;
}

// Pool/worker message types for real-time diagnostics
export interface PoolHeartbeatMessage {
	type: "worker_heartbeat";
	worker_id: string;
	hostname?: string;
	status?: string;
	started_at?: string;
	timestamp?: string;
	pool_size?: number;
	idle_count?: number;
	busy_count?: number;
	requirements_installed?: number;
	requirements_total?: number;
	memory_current_bytes?: number;
	memory_max_bytes?: number;
	processes?: Array<{
		process_id: string;
		pid: number;
		state: "idle" | "busy" | "killed";
		memory_mb: number;
		uptime_seconds: number;
		executions_completed: number;
		pending_recycle?: boolean;
		execution?: {
			execution_id: string;
			started_at: string;
			elapsed_seconds: number;
		};
	}>;
}

export interface PoolOnlineMessage {
	type: "worker_online";
	worker_id: string;
	hostname?: string;
	started_at?: string;
}

export interface PoolOfflineMessage {
	type: "worker_offline";
	worker_id: string;
}

export interface ProcessStateChangedMessage {
	type: "process_state_changed";
	worker_id: string;
	process_id: string;
	pid: number;
	old_state: "idle" | "busy" | "killed";
	new_state: "idle" | "busy" | "killed";
}

export interface PoolConfigChangedMessage {
	type: "pool_config_changed";
	worker_id: string;
	old_min: number;
	old_max: number;
	new_min: number;
	new_max: number;
}

export interface PoolScalingMessage {
	type: "pool_scaling";
	worker_id: string;
	action: "scale_up" | "scale_down" | "recycle_all";
	processes_affected: number;
}

export interface PoolProgressMessage {
	type: "pool_progress";
	worker_id: string;
	action: "scale_up" | "scale_down" | "recycle_all";
	current: number;
	total: number;
	message: string;
	timestamp: string;
}

export type PoolMessage =
	| PoolHeartbeatMessage
	| PoolOnlineMessage
	| PoolOfflineMessage
	| ProcessStateChangedMessage
	| PoolConfigChangedMessage
	| PoolScalingMessage
	| PoolProgressMessage;

// Message types from backend
type WebSocketMessage =
	| { type: "connected"; channels: string[]; userId: string }
	| { type: "connected"; executionId: string }
	| { type: "subscribed"; channel: string }
	| { type: "unsubscribed"; channel: string }
	| { type: "pong" }
	| { type: "execution_update"; executionId: string; [key: string]: unknown }
	| { type: "execution_log"; executionId: string; [key: string]: unknown }
	| { type: "history_update"; [key: string]: unknown }
	| { type: "notification_created"; notification: NotificationPayload }
	| { type: "notification_updated"; notification: NotificationPayload }
	| { type: "notification_dismissed"; notification_id: string }
	| {
			type: "progress";
			action: "install" | "uninstall";
			line: string;
			total: number;
			installing: number;
			installed: number;
			recycling: number;
			recycled: number;
			failed: number;
			failures: { worker: string; package: string | null; error: string | null }[];
	  }
	| { type: "git_log"; jobId: string; level: string; message: string }
	| { type: "git_progress"; jobId: string; phase: string; current: number; total: number; path?: string | null }
	| { type: "git_complete"; jobId: string; status: "success" | "error"; message: string; [key: string]: unknown }
	| { type: "git_op_complete"; jobId: string; status: string; resultType: string; data?: Record<string, unknown>; error?: string }
	| {
			type: "devrun_state_update";
			state: LocalRunnerStateUpdate | null;
	  }
	| {
			type: "local_runner_state_update";
			state: LocalRunnerStateUpdate | null;
	  }
	| {
			type: "event_created" | "event_updated";
			event: EventSourceEvent;
	  }
	| ChatStreamChunk
	| AppDraftUpdate
	| AppCodeFileUpdate
	| AppPublishedUpdate
	| PoolMessage
	| FileActivityEvent
	| AgentRunUpdate
	| AgentRunStepUpdate
	| SummaryBackfillUpdate;

// Notification payload from backend (snake_case)
interface NotificationPayload {
	id: string;
	category: string;
	title: string;
	description: string | null;
	status: string;
	percent: number | null;
	error: string | null;
	result: Record<string, unknown> | null;
	metadata: Record<string, unknown> | null;
	created_at: string;
	updated_at: string;
	user_id: string;
}

type ExecutionUpdateCallback = (update: ExecutionUpdate) => void;
type ExecutionLogCallback = (log: ExecutionLog) => void;
type NewExecutionCallback = (execution: NewExecution) => void;
type HistoryUpdateCallback = (update: HistoryUpdate) => void;
type PackageProgressCallback = (p: PackageProgress) => void;
// Git sync progress type
export interface GitProgress {
	phase: string;
	current: number;
	total: number;
	path?: string | null;
}

// Git operation complete type
export interface GitOpComplete {
	status: string;
	resultType: string;
	data?: Record<string, unknown>;
	error?: string;
}

type GitLogCallback = (log: PackageLog) => void;
type GitProgressCallback = (progress: GitProgress) => void;
type GitCompleteCallback = (complete: PackageComplete & Record<string, unknown>) => void;
type LocalRunnerStateCallback = (state: LocalRunnerStateUpdate | null) => void;
type EventSourceUpdateCallback = (update: EventSourceUpdate) => void;
type ChatStreamCallback = (chunk: ChatStreamChunk) => void;
type AppDraftUpdateCallback = (update: AppDraftUpdate) => void;
type AppCodeFileUpdateCallback = (update: AppCodeFileUpdate) => void;
type AppPublishedUpdateCallback = (update: AppPublishedUpdate) => void;
type PoolMessageCallback = (message: PoolMessage) => void;
type FileActivityCallback = (event: FileActivityEvent) => void;
type AgentRunUpdateCallback = (update: AgentRunUpdate) => void;
type SummaryBackfillUpdateCallback = (update: SummaryBackfillUpdate) => void;
type AgentRunStepCallback = (update: AgentRunStepUpdate) => void;

/**
 * Check if a JWT token is an embed token by inspecting its claims.
 */
function isEmbedToken(token: string): boolean {
	try {
		const parts = token.split(".");
		if (parts.length !== 3) return false;
		const payload = JSON.parse(atob(parts[1]));
		return payload.embed === true;
	} catch {
		return false;
	}
}

class WebSocketService {
	private ws: WebSocket | null = null;
	private connectionPromise: Promise<void> | null = null;
	private isConnecting = false;
	private retryCount = 0;
	private maxRetries = 3;
	private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
	private pingInterval: ReturnType<typeof setInterval> | null = null;
	private userId: string | null = null;

	// Subscribers for different event types
	private executionUpdateCallbacks = new Map<
		string,
		Set<ExecutionUpdateCallback>
	>();
	private executionLogCallbacks = new Map<
		string,
		Set<ExecutionLogCallback>
	>();
	private newExecutionCallbacks = new Set<NewExecutionCallback>();
	private historyUpdateCallbacks = new Set<HistoryUpdateCallback>();
	private packageProgressCallbacks = new Set<PackageProgressCallback>();
	private gitLogCallbacks = new Map<string, Set<GitLogCallback>>();
	private gitProgressCallbacks = new Map<string, Set<GitProgressCallback>>();
	private gitCompleteCallbacks = new Map<string, Set<GitCompleteCallback>>();
	private gitOpCompleteCallbacks = new Map<string, Set<(complete: GitOpComplete) => void>>();
	private localRunnerStateCallbacks = new Set<LocalRunnerStateCallback>();
	private eventSourceUpdateCallbacks = new Map<
		string,
		Set<EventSourceUpdateCallback>
	>();
	private chatStreamCallbacks = new Map<string, ChatStreamCallback>();
	private appDraftUpdateCallbacks = new Map<
		string,
		Set<AppDraftUpdateCallback>
	>();
	private appCodeFileUpdateCallbacks = new Map<
		string,
		Set<AppCodeFileUpdateCallback>
	>();
	private appPublishedUpdateCallbacks = new Map<
		string,
		Set<AppPublishedUpdateCallback>
	>();
	private poolMessageCallbacks = new Set<PoolMessageCallback>();
	private fileActivityCallbacks = new Set<FileActivityCallback>();
	private agentRunUpdateCallbacks: Set<AgentRunUpdateCallback> = new Set();
	private summaryBackfillCallbacks: Map<
		string,
		Set<SummaryBackfillUpdateCallback>
	> = new Map();
	private agentRunStepCallbacks: Map<string, Set<AgentRunStepCallback>> = new Map();
	private connectionStatusCallbacks = new Set<(connected: boolean) => void>();

	// Track subscribed channels
	private subscribedChannels = new Set<string>();
	private pendingSubscriptions = new Set<string>();

	/**
	 * Connect to WebSocket with authentication
	 */
	async connect(channels: string[] = []): Promise<void> {
		// If already connected, just subscribe to new channels
		if (this.ws?.readyState === WebSocket.OPEN) {
			for (const channel of channels) {
				if (!this.subscribedChannels.has(channel)) {
					await this.subscribe(channel);
				}
			}
			return;
		}

		// If already connecting, wait for that connection
		if (this.isConnecting && this.connectionPromise) {
			await this.connectionPromise;
			// Subscribe to channels after connection
			for (const channel of channels) {
				if (!this.subscribedChannels.has(channel)) {
					await this.subscribe(channel);
				}
			}
			return;
		}

		this.isConnecting = true;
		this.pendingSubscriptions = new Set(channels);
		this.connectionPromise = this._connect(channels);

		try {
			await this.connectionPromise;
		} finally {
			this.isConnecting = false;
			this.connectionPromise = null;
		}
	}

	private async _connect(channels: string[]): Promise<void> {
		try {
			// Build WebSocket URL with channels
			const protocol =
				window.location.protocol === "https:" ? "wss:" : "ws:";
			const host = window.location.host;

			// Add channels as query params
			const params = new URLSearchParams();
			channels.forEach((ch) => params.append("channels", ch));

			// For embed tokens, pass as query param since browser WebSocket
			// API can't set custom headers. getActiveToken() prefers the
			// per-tab embed token (sessionStorage) over the user token.
			const embedToken = getActiveToken();
			if (embedToken && isEmbedToken(embedToken)) {
				params.set("token", embedToken);
			}

			const wsUrl = `${protocol}//${host}/ws/connect?${params.toString()}`;

			// Create WebSocket connection
			// Note: Cookies (including access_token) are automatically sent by the browser
			this.ws = new WebSocket(wsUrl);

			// Set up WebSocket handlers
			this.ws.onopen = () => {
				this.retryCount = 0;
				this.startPingInterval();
				this.connectionStatusCallbacks.forEach((cb) => cb(true));
			};

			this.ws.onmessage = (event) => {
				try {
					const message = JSON.parse(event.data) as WebSocketMessage;
					this.handleMessage(message);
				} catch (error) {
					console.error(
						"[WebSocket] Failed to parse message:",
						error,
					);
				}
			};

			this.ws.onerror = (error) => {
				console.error("[WebSocket] Error:", error);
			};

			this.ws.onclose = (event) => {
				this.ws = null;
				this.stopPingInterval();
				this.connectionStatusCallbacks.forEach((cb) => cb(false));

				// Attempt to reconnect if not a normal closure
				if (event.code !== 1000 && this.retryCount < this.maxRetries) {
					this.retryCount++;
					const delay = Math.min(
						1000 * Math.pow(2, this.retryCount),
						30000,
					);
					this.reconnectTimeout = setTimeout(async () => {
						// If unauthorized (4001), refresh token before reconnecting
						// Browser will send fresh cookie on next WebSocket handshake
						if (event.code === 4001) {
							console.warn(
								"[WebSocket] Token expired, refreshing before reconnect",
							);
							await refreshAccessToken();
						}
						this.connect(Array.from(this.subscribedChannels));
					}, delay);
				}
			};

			// Wait for connection to open
			await new Promise<void>((resolve, reject) => {
				const timeout = setTimeout(() => {
					reject(new Error("WebSocket connection timeout"));
				}, 10000);

				if (this.ws) {
					this.ws.addEventListener(
						"open",
						() => {
							clearTimeout(timeout);
							resolve();
						},
						{ once: true },
					);
					this.ws.addEventListener(
						"error",
						(error) => {
							clearTimeout(timeout);
							reject(error);
						},
						{ once: true },
					);
				}
			});
		} catch (error) {
			console.error("[WebSocket] Failed to connect:", error);
			this.ws = null;
			throw error;
		}
	}

	/**
	 * Connect to a specific execution
	 */
	async connectToExecution(executionId: string): Promise<void> {
		// If already connected to this execution, return
		const channel = `execution:${executionId}`;
		if (this.subscribedChannels.has(channel)) {
			return;
		}

		// If WebSocket is open, subscribe to channel
		if (this.ws?.readyState === WebSocket.OPEN) {
			await this.subscribe(channel);
			return;
		}

		// Otherwise, connect with this channel
		await this.connect([channel]);
	}

	/**
	 * Handle incoming WebSocket messages
	 */
	private handleMessage(message: WebSocketMessage) {
		switch (message.type) {
			case "connected":
				if ("channels" in message) {
					// General connection confirmation
					message.channels.forEach((ch) =>
						this.subscribedChannels.add(ch),
					);
					this.userId = message.userId;
				} else if ("executionId" in message) {
					// Execution-specific connection
					this.subscribedChannels.add(
						`execution:${message.executionId}`,
					);
				}
				break;

			case "subscribed":
				this.subscribedChannels.add(message.channel);
				this.pendingSubscriptions.delete(message.channel);
				break;

			case "unsubscribed":
				this.subscribedChannels.delete(message.channel);
				break;

			case "pong":
				// Heartbeat response
				break;

			case "execution_update":
				this.dispatchExecutionUpdate(message);
				break;

			case "execution_log":
				this.dispatchExecutionLog(message);
				break;

			case "history_update":
				this.dispatchHistoryUpdate(message);
				break;

			case "notification_created":
			case "notification_updated":
				this.handleNotification(message.notification);
				break;

			case "notification_dismissed":
				useNotificationStore
					.getState()
					.removeNotification(message.notification_id);
				break;

			case "progress":
				// Aggregated package-install progress (one rolling line across workers)
				this.packageProgressCallbacks.forEach((cb) =>
					cb({
						action: message.action,
						line: message.line,
						total: message.total,
						installing: message.installing,
						installed: message.installed,
						recycling: message.recycling,
						recycled: message.recycled,
						failed: message.failed,
						failures: message.failures,
					}),
				);
				break;

			case "git_log": {
				// Git sync log message - dispatch to job-specific subscribers
				const gitLogJobId = message.jobId;
				if (gitLogJobId) {
					const callbacks = this.gitLogCallbacks.get(gitLogJobId);
					callbacks?.forEach((cb) =>
						cb({ level: message.level, message: message.message }),
					);
				}
				break;
			}

			case "git_progress": {
				// Git sync progress message - dispatch to job-specific subscribers
				const gitProgressJobId = message.jobId;
				if (gitProgressJobId) {
					const callbacks = this.gitProgressCallbacks.get(gitProgressJobId);
					callbacks?.forEach((cb) =>
						cb({
							phase: message.phase,
							current: message.current,
							total: message.total,
							path: message.path,
						}),
					);
				}
				break;
			}

			case "git_complete": {
				// Git sync complete message - dispatch to job-specific subscribers
				const gitCompleteJobId = message.jobId;
				if (gitCompleteJobId) {
					const { type: _type, jobId: _jobId, ...gitCompleteData } = message;
					const callbacks = this.gitCompleteCallbacks.get(gitCompleteJobId);
					callbacks?.forEach((cb) => cb(gitCompleteData as Parameters<GitCompleteCallback>[0]));
				}
				break;
			}

			case "git_op_complete": {
				const gitOpJobId = message.jobId;
				if (gitOpJobId) {
					const callbacks = this.gitOpCompleteCallbacks.get(gitOpJobId);
					callbacks?.forEach((cb) => cb({
						status: message.status,
						resultType: message.resultType,
						data: message.data,
						error: message.error,
					}));
				}
				break;
			}

			case "devrun_state_update":
				// Dev run state update from CLI (legacy)
				this.localRunnerStateCallbacks.forEach((cb) =>
					cb(message.state),
				);
				break;

			case "local_runner_state_update":
				// Local runner state update from CLI
				this.localRunnerStateCallbacks.forEach((cb) =>
					cb(message.state),
				);
				break;

			case "event_created":
			case "event_updated":
				// Event source updates (real-time events)
				this.dispatchEventSourceUpdate(message);
				break;

			// Chat streaming message types
			case "message_start":
			case "delta":
			case "tool_call":
			case "tool_progress":
			case "tool_result":
			case "agent_switch":
			case "done":
			case "error":
			case "title_update":
			case "ask_user_question":
			case "assistant_message_start":
			case "assistant_message_end":
			case "todo_update":
				this.dispatchChatStreamChunk(message as ChatStreamChunk);
				break;

			// App Builder live update message types
			case "app_draft_update":
				this.dispatchAppDraftUpdate(message as AppDraftUpdate);
				break;

			case "app_code_file_update":
				this.dispatchAppCodeFileUpdate(message as AppCodeFileUpdate);
				break;

			case "app_published":
				this.dispatchAppPublished(message as AppPublishedUpdate);
				break;

			// File activity events from CLI push/watch
			case "file_push":
			case "watch_start":
			case "watch_stop":
				this.fileActivityCallbacks.forEach((cb) => cb(message as FileActivityEvent));
				break;

			// Agent run update types
			case "agent_run_update": {
				const agentRunUpdate = message as unknown as AgentRunUpdate;
				this.agentRunUpdateCallbacks.forEach((cb) => cb(agentRunUpdate));
				break;
			}
			case "agent_run_step": {
				const agentRunStepUpdate = message as unknown as AgentRunStepUpdate;
				const runCallbacks = this.agentRunStepCallbacks.get(agentRunStepUpdate.run_id);
				if (runCallbacks) {
					runCallbacks.forEach((cb) => cb(agentRunStepUpdate));
				}
				break;
			}
			case "summary_backfill_update": {
				const bfUpdate = message as unknown as SummaryBackfillUpdate;
				const jobCallbacks = this.summaryBackfillCallbacks.get(
					bfUpdate.job_id,
				);
				if (jobCallbacks) {
					jobCallbacks.forEach((cb) => cb(bfUpdate));
				}
				break;
			}

			// Pool/worker message types for diagnostics
			case "worker_heartbeat":
			case "worker_online":
			case "worker_offline":
			case "process_state_changed":
			case "pool_config_changed":
			case "pool_scaling":
			case "pool_progress":
				this.dispatchPoolMessage(message as PoolMessage);
				break;
		}
	}

	private dispatchPoolMessage(message: PoolMessage) {
		this.poolMessageCallbacks.forEach((cb) => cb(message));
	}

	private dispatchEventSourceUpdate(message: {
		type: "event_created" | "event_updated";
		event: EventSourceEvent;
	}) {
		const sourceId = message.event.event_source_id;
		const update: EventSourceUpdate = {
			type: message.type,
			event: message.event,
		};

		// Dispatch to source-specific callbacks
		const callbacks = this.eventSourceUpdateCallbacks.get(sourceId);
		callbacks?.forEach((cb) => cb(update));
	}

	private dispatchChatStreamChunk(chunk: ChatStreamChunk) {
		const conversationId = chunk.conversation_id;
		if (!conversationId) return;

		// Dispatch to single conversation callback (no duplicates possible)
		const callback = this.chatStreamCallbacks.get(conversationId);
		callback?.(chunk);
	}

	private dispatchAppDraftUpdate(update: AppDraftUpdate) {
		const appId = update.appId;
		const callbacks = this.appDraftUpdateCallbacks.get(appId);
		callbacks?.forEach((cb) => cb(update));
	}

	private dispatchAppCodeFileUpdate(update: AppCodeFileUpdate) {
		const appId = update.appId;
		const callbacks = this.appCodeFileUpdateCallbacks.get(appId);
		callbacks?.forEach((cb) => cb(update));
	}

	private dispatchAppPublished(update: AppPublishedUpdate) {
		const appId = update.appId;
		const callbacks = this.appPublishedUpdateCallbacks.get(appId);
		callbacks?.forEach((cb) => cb(update));
	}

	private dispatchExecutionUpdate(
		message: { type: "execution_update"; executionId: string } & Record<
			string,
			unknown
		>,
	) {
		const status = message["status"] as string;
		const timestamp =
			(message["timestamp"] as string) || new Date().toISOString();
		const result = message["result"];
		const error = message["error"] as string | undefined;
		const durationMs = message["duration_ms"] as number | undefined;

		// Queue visibility fields
		const queuePosition = message["queuePosition"] as number | undefined;
		const waitReason = message["waitReason"] as WaitReason | undefined;
		const availableMemoryMb = message["availableMemoryMb"] as
			| number
			| undefined;
		const requiredMemoryMb = message["requiredMemoryMb"] as
			| number
			| undefined;

		const update: ExecutionUpdate = {
			executionId: message.executionId,
			status,
			isComplete:
				status === "Success" ||
				status === "Failed" ||
				status === "CompletedWithErrors" ||
				status === "Timeout" ||
				status === "Cancelled",
			timestamp,
			result,
			...(error !== undefined ? { error } : {}),
			...(durationMs !== undefined ? { duration_ms: durationMs } : {}),
			...(queuePosition !== undefined ? { queuePosition } : {}),
			...(waitReason !== undefined ? { waitReason } : {}),
			...(availableMemoryMb !== undefined ? { availableMemoryMb } : {}),
			...(requiredMemoryMb !== undefined ? { requiredMemoryMb } : {}),
		};

		// Dispatch to execution-specific callbacks
		const callbacks = this.executionUpdateCallbacks.get(
			message.executionId,
		);
		callbacks?.forEach((cb) => cb(update));

		// Dispatch to global callbacks (for history page)
		const completedAt = message["completed_at"] as string | undefined;
		const historyUpdate: HistoryUpdate = {
			execution_id: update.executionId,
			workflow_name: (message["workflow_name"] as string) || "",
			status: update.status,
			executed_by: (message["executed_by"] as string) || "",
			executed_by_name: (message["executed_by_name"] as string) || "",
			started_at: (message["started_at"] as string) || "",
			timestamp: update.timestamp,
			...(completedAt !== undefined ? { completed_at: completedAt } : {}),
			...(durationMs !== undefined ? { duration_ms: durationMs } : {}),
		};
		this.historyUpdateCallbacks.forEach((cb) => cb(historyUpdate));
	}

	private dispatchHistoryUpdate(
		message: { type: "history_update" } & Record<string, unknown>,
	) {
		const historyUpdate: HistoryUpdate = {
			execution_id: (message["execution_id"] as string) || "",
			workflow_name: (message["workflow_name"] as string) || "",
			status: (message["status"] as string) || "",
			executed_by: (message["executed_by"] as string) || "",
			executed_by_name: (message["executed_by_name"] as string) || "",
			org_id: message["org_id"] as string | undefined,
			started_at: (message["started_at"] as string) || "",
			completed_at: message["completed_at"] as string | undefined,
			duration_ms: message["duration_ms"] as number | undefined,
			timestamp:
				(message["timestamp"] as string) || new Date().toISOString(),
		};
		this.historyUpdateCallbacks.forEach((cb) => cb(historyUpdate));
	}

	private dispatchExecutionLog(
		message: { type: "execution_log"; executionId: string } & Record<
			string,
			unknown
		>,
	) {
		const sequence = message["sequence"] as number | undefined;
		const data = message["data"] as Record<string, unknown> | undefined;

		const log: ExecutionLog = {
			executionId: message.executionId,
			timestamp:
				(message["timestamp"] as string) || new Date().toISOString(),
			level: (message["level"] as string) || "info",
			message: (message["message"] as string) || "",
			...(sequence !== undefined ? { sequence } : {}),
			...(data !== undefined ? { data } : {}),
		};

		const callbacks = this.executionLogCallbacks.get(message.executionId);
		callbacks?.forEach((cb) => cb(log));
	}

	/**
	 * Handle notification message from backend
	 */
	private handleNotification(payload: NotificationPayload) {
		console.warn(
			"[WS] Notification received:",
			payload.status,
			payload.id,
			payload,
		);

		// Convert snake_case to camelCase for frontend
		const notification: Notification = {
			id: payload.id,
			category: payload.category as Notification["category"],
			title: payload.title,
			description: payload.description,
			status: payload.status as Notification["status"],
			percent: payload.percent,
			error: payload.error,
			result: payload.result,
			metadata: payload.metadata,
			createdAt: payload.created_at,
			updatedAt: payload.updated_at,
			userId: payload.user_id,
		};

		useNotificationStore.getState().setNotification(notification);
	}

	/**
	 * Subscribe to a channel
	 */
	async subscribe(channel: string): Promise<void> {
		if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
			this.pendingSubscriptions.add(channel);
			return;
		}

		this.ws.send(
			JSON.stringify({
				type: "subscribe",
				channels: [channel],
			}),
		);
	}

	/**
	 * Unsubscribe from a channel
	 */
	async unsubscribe(channel: string): Promise<void> {
		if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
			return;
		}

		this.ws.send(
			JSON.stringify({
				type: "unsubscribe",
				channel,
			}),
		);
	}

	/**
	 * Subscribe to execution updates for a specific execution
	 */
	onExecutionUpdate(
		executionId: string,
		callback: ExecutionUpdateCallback,
	): () => void {
		if (!this.executionUpdateCallbacks.has(executionId)) {
			this.executionUpdateCallbacks.set(executionId, new Set());
		}
		this.executionUpdateCallbacks.get(executionId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.executionUpdateCallbacks.get(executionId)?.delete(callback);
			if (this.executionUpdateCallbacks.get(executionId)?.size === 0) {
				this.executionUpdateCallbacks.delete(executionId);
			}
		};
	}

	/**
	 * Subscribe to execution logs for a specific execution
	 */
	onExecutionLog(
		executionId: string,
		callback: ExecutionLogCallback,
	): () => void {
		if (!this.executionLogCallbacks.has(executionId)) {
			this.executionLogCallbacks.set(executionId, new Set());
		}
		this.executionLogCallbacks.get(executionId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.executionLogCallbacks.get(executionId)?.delete(callback);
			if (this.executionLogCallbacks.get(executionId)?.size === 0) {
				this.executionLogCallbacks.delete(executionId);
			}
		};
	}

	/**
	 * Subscribe to new execution notifications
	 */
	onNewExecution(callback: NewExecutionCallback): () => void {
		this.newExecutionCallbacks.add(callback);
		return () => {
			this.newExecutionCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to history page updates
	 */
	onHistoryUpdate(callback: HistoryUpdateCallback): () => void {
		this.historyUpdateCallbacks.add(callback);
		return () => {
			this.historyUpdateCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to aggregated package-install progress events
	 */
	onPackageProgress(callback: PackageProgressCallback): () => void {
		this.packageProgressCallbacks.add(callback);
		return () => {
			this.packageProgressCallbacks.delete(callback);
		};
	}

	/**
	 * Connect to a git sync channel for progress updates
	 */
	async connectToGitSync(connectionId: string): Promise<void> {
		const channel = `git:${connectionId}`;
		if (this.subscribedChannels.has(channel)) {
			return;
		}

		if (this.ws?.readyState === WebSocket.OPEN) {
			await this.subscribe(channel);
			return;
		}

		await this.connect([channel]);
	}

	/**
	 * Subscribe to git sync log messages for a specific connection
	 */
	onGitSyncLog(connectionId: string, callback: GitLogCallback): () => void {
		if (!this.gitLogCallbacks.has(connectionId)) {
			this.gitLogCallbacks.set(connectionId, new Set());
		}
		this.gitLogCallbacks.get(connectionId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.gitLogCallbacks.get(connectionId)?.delete(callback);
			if (this.gitLogCallbacks.get(connectionId)?.size === 0) {
				this.gitLogCallbacks.delete(connectionId);
			}
		};
	}

	/**
	 * Subscribe to git sync progress updates for a specific connection
	 */
	onGitSyncProgress(connectionId: string, callback: GitProgressCallback): () => void {
		if (!this.gitProgressCallbacks.has(connectionId)) {
			this.gitProgressCallbacks.set(connectionId, new Set());
		}
		this.gitProgressCallbacks.get(connectionId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.gitProgressCallbacks.get(connectionId)?.delete(callback);
			if (this.gitProgressCallbacks.get(connectionId)?.size === 0) {
				this.gitProgressCallbacks.delete(connectionId);
			}
		};
	}

	/**
	 * Subscribe to git sync completion for a specific connection
	 */
	onGitSyncComplete(connectionId: string, callback: GitCompleteCallback): () => void {
		if (!this.gitCompleteCallbacks.has(connectionId)) {
			this.gitCompleteCallbacks.set(connectionId, new Set());
		}
		this.gitCompleteCallbacks.get(connectionId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.gitCompleteCallbacks.get(connectionId)?.delete(callback);
			if (this.gitCompleteCallbacks.get(connectionId)?.size === 0) {
				this.gitCompleteCallbacks.delete(connectionId);
			}
		};
	}

	/**
	 * Subscribe to git operation progress updates for a specific job
	 */
	onGitProgress(
		jobId: string,
		callback: GitProgressCallback,
	): () => void {
		if (!this.gitProgressCallbacks.has(jobId)) {
			this.gitProgressCallbacks.set(jobId, new Set());
		}
		this.gitProgressCallbacks.get(jobId)!.add(callback);

		return () => {
			this.gitProgressCallbacks.get(jobId)?.delete(callback);
			if (this.gitProgressCallbacks.get(jobId)?.size === 0) {
				this.gitProgressCallbacks.delete(jobId);
			}
		};
	}

	/**
	 * Subscribe to git operation completion for a specific job
	 */
	onGitOpComplete(
		jobId: string,
		callback: (complete: GitOpComplete) => void,
	): () => void {
		if (!this.gitOpCompleteCallbacks.has(jobId)) {
			this.gitOpCompleteCallbacks.set(jobId, new Set());
		}
		this.gitOpCompleteCallbacks.get(jobId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.gitOpCompleteCallbacks.get(jobId)?.delete(callback);
			if (this.gitOpCompleteCallbacks.get(jobId)?.size === 0) {
				this.gitOpCompleteCallbacks.delete(jobId);
			}
		};
	}

	/**
	 * Subscribe to local runner state updates
	 */
	onLocalRunnerState(callback: LocalRunnerStateCallback): () => void {
		this.localRunnerStateCallbacks.add(callback);
		return () => {
			this.localRunnerStateCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to event source updates for real-time event streaming
	 */
	onEventSourceUpdate(
		sourceId: string,
		callback: EventSourceUpdateCallback,
	): () => void {
		if (!this.eventSourceUpdateCallbacks.has(sourceId)) {
			this.eventSourceUpdateCallbacks.set(sourceId, new Set());
		}
		this.eventSourceUpdateCallbacks.get(sourceId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.eventSourceUpdateCallbacks.get(sourceId)?.delete(callback);
			if (this.eventSourceUpdateCallbacks.get(sourceId)?.size === 0) {
				this.eventSourceUpdateCallbacks.delete(sourceId);
			}
		};
	}

	/**
	 * Subscribe to chat stream chunks for a specific conversation.
	 * Replaces any existing callback - only one callback per conversation.
	 */
	onChatStream(
		conversationId: string,
		callback: ChatStreamCallback,
	): () => void {
		// Simply set (replaces any existing - no duplicates possible)
		this.chatStreamCallbacks.set(conversationId, callback);

		// Return unsubscribe function
		return () => {
			// Only delete if this is still the registered callback
			if (this.chatStreamCallbacks.get(conversationId) === callback) {
				this.chatStreamCallbacks.delete(conversationId);
			}
		};
	}

	/**
	 * Connect to a chat conversation channel
	 */
	async connectToChat(conversationId: string): Promise<void> {
		const channel = `chat:${conversationId}`;
		if (this.subscribedChannels.has(channel)) {
			return;
		}

		if (this.ws?.readyState === WebSocket.OPEN) {
			await this.subscribe(channel);
			return;
		}

		await this.connect([channel]);
	}

	/**
	 * Send a chat message to a conversation
	 */
	sendChatMessage(conversationId: string, message: string, localId?: string): boolean {
		if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
			return false;
		}

		this.ws.send(
			JSON.stringify({
				type: "chat",
				conversation_id: conversationId,
				message,
				local_id: localId,
			}),
		);
		return true;
	}

	/**
	 * Send an answer to an AskUserQuestion prompt from the SDK
	 */
	sendChatAnswer(
		conversationId: string,
		requestId: string,
		answers: Record<string, string>,
	): boolean {
		if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
			return false;
		}

		this.ws.send(
			JSON.stringify({
				type: "chat_answer",
				conversation_id: conversationId,
				request_id: requestId,
				answers,
			}),
		);
		return true;
	}

	/**
	 * Send a stop signal to interrupt the current chat operation
	 */
	sendChatStop(conversationId: string): boolean {
		if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
			return false;
		}

		this.ws.send(
			JSON.stringify({
				type: "chat_stop",
				conversation_id: conversationId,
			}),
		);
		return true;
	}

	/**
	 * Connect to an app's draft channel for live updates
	 */
	async connectToAppDraft(appId: string): Promise<void> {
		const channel = `app:draft:${appId}`;
		if (this.subscribedChannels.has(channel)) {
			return;
		}

		if (this.ws?.readyState === WebSocket.OPEN) {
			await this.subscribe(channel);
			return;
		}

		await this.connect([channel]);
	}

	/**
	 * Connect to an app's live channel for publish notifications
	 */
	async connectToAppLive(appId: string): Promise<void> {
		const channel = `app:live:${appId}`;
		if (this.subscribedChannels.has(channel)) {
			return;
		}

		if (this.ws?.readyState === WebSocket.OPEN) {
			await this.subscribe(channel);
			return;
		}

		await this.connect([channel]);
	}

	/**
	 * Subscribe to draft updates for an app (components engine)
	 */
	onAppDraftUpdate(
		appId: string,
		callback: AppDraftUpdateCallback,
	): () => void {
		if (!this.appDraftUpdateCallbacks.has(appId)) {
			this.appDraftUpdateCallbacks.set(appId, new Set());
		}
		this.appDraftUpdateCallbacks.get(appId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.appDraftUpdateCallbacks.get(appId)?.delete(callback);
			if (this.appDraftUpdateCallbacks.get(appId)?.size === 0) {
				this.appDraftUpdateCallbacks.delete(appId);
			}
		};
	}

	/**
	 * Subscribe to code file updates for an app (code engine)
	 * Includes full source/compiled content for real-time preview
	 */
	onAppCodeFileUpdate(
		appId: string,
		callback: AppCodeFileUpdateCallback,
	): () => void {
		if (!this.appCodeFileUpdateCallbacks.has(appId)) {
			this.appCodeFileUpdateCallbacks.set(appId, new Set());
		}
		this.appCodeFileUpdateCallbacks.get(appId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.appCodeFileUpdateCallbacks.get(appId)?.delete(callback);
			if (this.appCodeFileUpdateCallbacks.get(appId)?.size === 0) {
				this.appCodeFileUpdateCallbacks.delete(appId);
			}
		};
	}

	/**
	 * Subscribe to publish events for an app
	 */
	onAppPublished(
		appId: string,
		callback: AppPublishedUpdateCallback,
	): () => void {
		if (!this.appPublishedUpdateCallbacks.has(appId)) {
			this.appPublishedUpdateCallbacks.set(appId, new Set());
		}
		this.appPublishedUpdateCallbacks.get(appId)!.add(callback);

		// Return unsubscribe function
		return () => {
			this.appPublishedUpdateCallbacks.get(appId)?.delete(callback);
			if (this.appPublishedUpdateCallbacks.get(appId)?.size === 0) {
				this.appPublishedUpdateCallbacks.delete(appId);
			}
		};
	}

	/**
	 * Subscribe to pool/worker updates for diagnostics
	 */
	onPoolMessage(callback: PoolMessageCallback): () => void {
		this.poolMessageCallbacks.add(callback);
		return () => {
			this.poolMessageCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to file activity events (push/watch from CLI)
	 */
	onFileActivity(callback: FileActivityCallback): () => void {
		this.fileActivityCallbacks.add(callback);
		return () => {
			this.fileActivityCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to agent run updates (list page)
	 */
	onAgentRunUpdate(callback: AgentRunUpdateCallback): () => void {
		this.agentRunUpdateCallbacks.add(callback);
		return () => {
			this.agentRunUpdateCallbacks.delete(callback);
		};
	}

	/**
	 * Subscribe to agent run step updates (detail page)
	 */
	onAgentRunStep(
		runId: string,
		callback: AgentRunStepCallback,
	): () => void {
		if (!this.agentRunStepCallbacks.has(runId)) {
			this.agentRunStepCallbacks.set(runId, new Set());
		}
		this.agentRunStepCallbacks.get(runId)!.add(callback);
		return () => {
			this.agentRunStepCallbacks.get(runId)?.delete(callback);
			if (this.agentRunStepCallbacks.get(runId)?.size === 0) {
				this.agentRunStepCallbacks.delete(runId);
			}
		};
	}

	/**
	 * Subscribe to progress updates for a summary backfill job.
	 */
	onSummaryBackfillUpdate(
		jobId: string,
		callback: SummaryBackfillUpdateCallback,
	): () => void {
		if (!this.summaryBackfillCallbacks.has(jobId)) {
			this.summaryBackfillCallbacks.set(jobId, new Set());
		}
		this.summaryBackfillCallbacks.get(jobId)!.add(callback);
		return () => {
			this.summaryBackfillCallbacks.get(jobId)?.delete(callback);
			if (this.summaryBackfillCallbacks.get(jobId)?.size === 0) {
				this.summaryBackfillCallbacks.delete(jobId);
			}
		};
	}

	/**
	 * Start ping interval for keeping connection alive.
	 * 15 seconds is a safe interval that works with most load balancers
	 * (GKE defaults to 30s, CloudFlare to 100s).
	 */
	private startPingInterval() {
		this.pingInterval = setInterval(() => {
			if (this.ws?.readyState === WebSocket.OPEN) {
				this.ws.send(JSON.stringify({ type: "ping" }));
			}
		}, 15000);
	}

	/**
	 * Stop ping interval
	 */
	private stopPingInterval() {
		if (this.pingInterval) {
			clearInterval(this.pingInterval);
			this.pingInterval = null;
		}
	}

	/**
	 * Disconnect from WebSocket
	 */
	async disconnect(): Promise<void> {
		if (this.reconnectTimeout) {
			clearTimeout(this.reconnectTimeout);
			this.reconnectTimeout = null;
		}

		this.stopPingInterval();

		if (this.ws) {
			this.subscribedChannels.clear();
			this.ws.close(1000, "Normal closure");
			this.ws = null;
		}
	}

	/**
	 * Check if currently connected
	 */
	isConnected(): boolean {
		return this.ws?.readyState === WebSocket.OPEN;
	}

	/**
	 * Subscribe to connection status changes
	 */
	onConnectionStatusChange(callback: (connected: boolean) => void): () => void {
		this.connectionStatusCallbacks.add(callback);
		return () => {
			this.connectionStatusCallbacks.delete(callback);
		};
	}

	/**
	 * Get the current user ID
	 */
	getUserId(): string | null {
		return this.userId;
	}
}

// Export singleton instance
export const webSocketService = new WebSocketService();

// Also export as webPubSubService for backwards compatibility
export const webPubSubService = webSocketService;
