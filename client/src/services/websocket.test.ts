import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	ACCESS_TOKEN_KEY,
	EMBED_TOKEN_KEY,
	setImpersonationToken,
} from "@/lib/auth-token";
import { type PlatformJobUpdate, webSocketService } from "./websocket";

class MockWebSocket {
	static readonly OPEN = 1;
	static urls: string[] = [];
	readonly readyState = MockWebSocket.OPEN;
	onopen: ((event: Event) => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	private listeners = new Map<string, Set<(event: Event) => void>>();

	constructor(url: string) {
		MockWebSocket.urls.push(url);
		queueMicrotask(() => this.emit("open"));
	}

	addEventListener(type: string, listener: (event: Event) => void) {
		const listeners = this.listeners.get(type) ?? new Set();
		listeners.add(listener);
		this.listeners.set(type, listeners);
	}

	send() {}

	close() {
		this.emit("close", { code: 1000 } as CloseEvent);
	}

	private emit(type: string, event: Event = new Event(type)) {
		if (type === "open") this.onopen?.(event);
		if (type === "close") this.onclose?.(event as CloseEvent);
		for (const listener of this.listeners.get(type) ?? []) listener(event);
	}
}

const socketService = webSocketService as unknown as {
	_connect(channels: string[]): Promise<void>;
	disconnect(): Promise<void>;
};

beforeEach(() => {
	localStorage.clear();
	sessionStorage.clear();
	MockWebSocket.urls = [];
	vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(async () => {
	await socketService.disconnect();
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
});

describe("platform WebSocket authentication", () => {
	it("keeps the impersonation handshake free of a bearer query token", async () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
		setImpersonationToken("impersonated-token");

		await socketService._connect(["notifications"]);

		const url = new URL(MockWebSocket.urls[0]);
		expect(url.searchParams.has("token")).toBe(false);
		expect(url.searchParams.getAll("channels")).toEqual(["notifications"]);
	});

	it("keeps the normal session cookie-only handshake free of a token query", async () => {
		localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");

		await socketService._connect(["notifications"]);

		const url = new URL(MockWebSocket.urls[0]);
		expect(url.searchParams.has("token")).toBe(false);
	});

	it("keeps the existing embed token handshake in the query", async () => {
		const embedToken = `header.${btoa(JSON.stringify({ embed: true }))}.signature`;
		sessionStorage.setItem(EMBED_TOKEN_KEY, embedToken);

		await socketService._connect(["notifications"]);

		const url = new URL(MockWebSocket.urls[0]);
		expect(url.searchParams.get("token")).toBe(embedToken);
	});
});

describe("platform-job WebSocket contract", () => {
	it("dispatches the same durable snapshot by job id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onPlatformJobUpdate(
			"job-1",
			callback,
		);
		const job = {
			id: "job-1",
			job_type: "application.publish",
			payload_version: 1,
			organization_id: null,
			resource_type: "application",
			resource_id: "app-1",
			resource_lock_key: null,
			priority: 100,
			title: "Publishing Test",
			action_url: "/apps/test/edit",
			execution_backend: "local",
			requested_by_user_id: "user-1",
			requested_by_name: "Dev",
			status: "running",
			progress: {
				phase: "promoting current bundle",
				current: 2,
				total: 3,
				percent: 66,
			},
			revision: 4,
			attempt: 1,
			max_attempts: 2,
			can_cancel: false,
			result: null,
			error: null,
			notification_id: "notification-1",
			started_at: "2026-07-28T12:00:00Z",
			completed_at: null,
			created_at: "2026-07-28T12:00:00Z",
			updated_at: "2026-07-28T12:00:01Z",
		} satisfies PlatformJobUpdate;

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({ type: "platform_job_updated", job });

		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith(job);
		unsubscribe();
	});

	it("dispatches updates to global observers", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onAnyPlatformJobUpdate(callback);
		const job = {
			id: "job-2",
			job_type: "solution.deploy",
			payload_version: 1,
			organization_id: null,
			resource_type: "solution_deploy",
			resource_id: "deploy-1",
			resource_lock_key: null,
			priority: 500,
			title: "Solution deploy",
			action_url: "/solutions/solution-1",
			execution_backend: "local",
			requested_by_user_id: "user-1",
			requested_by_name: "Dev",
			status: "queued",
			progress: { phase: "Queued", current: 0, total: null, percent: 0 },
			revision: 1,
			attempt: 0,
			max_attempts: 2,
			can_cancel: true,
			result: null,
			error: null,
			notification_id: null,
			started_at: null,
			completed_at: null,
			created_at: "2026-08-23T12:00:00Z",
			updated_at: "2026-08-23T12:00:00Z",
		} satisfies PlatformJobUpdate;

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({ type: "platform_job_updated", job });

		expect(callback).toHaveBeenCalledWith(job);
		unsubscribe();
	});
});

describe("chat WebSocket contract", () => {
	it("dispatches durable chat run envelopes by conversation", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onChatStream(
			"conversation-1",
			callback,
		);
		const event = {
			type: "chat_run_event",
			protocol_version: 1,
			event_id: "event-1",
			sequence: 2,
			conversation_id: "conversation-1",
			run_id: "run-1",
			occurred_at: "2026-09-02T12:00:00Z",
			kind: "delta",
			status: "running",
			payload: { type: "delta", content: "hello" },
		};

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage(event);

		expect(callback).toHaveBeenCalledWith(event);
		unsubscribe();
	});

	it("reopens a closed socket even when the chat channel is remembered", async () => {
		const service = webSocketService as unknown as {
			ws: WebSocket | null;
			subscribedChannels: Set<string>;
			connect(channels: string[]): Promise<void>;
			connectToChat(conversationId: string): Promise<void>;
		};
		const channel = "chat:conversation-reconnect";
		service.ws = null;
		service.subscribedChannels.add(channel);
		const connect = vi
			.spyOn(service, "connect")
			.mockResolvedValue(undefined);

		await service.connectToChat("conversation-reconnect");

		expect(connect).toHaveBeenCalledWith([channel]);
		service.subscribedChannels.delete(channel);
		connect.mockRestore();
	});

	it("does not dispatch unversioned direct chat chunks", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onChatStream(
			"conversation-strict",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "delta",
			conversation_id: "conversation-strict",
			content: "legacy",
		});

		expect(callback).not.toHaveBeenCalled();
		unsubscribe();
	});
});

describe("service log streaming contract", () => {
	it("dispatches bridged attempt lines by service id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onServiceLog(
			"svc-1",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "service_log",
			service_id: "svc-1",
			attempt_id: "att-1",
			level: "INFO",
			message: "bridged live",
			timestamp: "2026-09-21T12:00:00+00:00",
		});

		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith({
			serviceId: "svc-1",
			attemptId: "att-1",
			timestamp: "2026-09-21T12:00:00+00:00",
			level: "INFO",
			message: "bridged live",
		});
		unsubscribe();
	});

	it("does not leak lines across services", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onServiceLog(
			"svc-1",
			callback,
		);

		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "service_log",
			service_id: "svc-2",
			attempt_id: "att-9",
			level: "INFO",
			message: "other service",
			timestamp: "2026-09-21T12:00:00+00:00",
		});

		expect(callback).not.toHaveBeenCalled();
		unsubscribe();
	});
});
