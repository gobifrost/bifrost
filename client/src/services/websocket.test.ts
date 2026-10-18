import { describe, expect, it, vi } from "vitest";
import { type PlatformJobUpdate, webSocketService } from "./websocket";

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

describe("channel subscription lifecycle", () => {
	it("subscribes channels requested before the connection acknowledgement without releasing implicit channels", async () => {
		const service = webSocketService as unknown as {
			ws: Pick<WebSocket, "readyState" | "send"> | null;
			subscribedChannels: Set<string>;
			subscribe(channel: string): Promise<void>;
			unsubscribe(channel: string): Promise<void>;
			handleMessage(message: unknown): void;
		};
		const requestedChannel = "notification:before-connected";
		const implicitChannel = "user:server-provided";
		const send = vi.fn();

		try {
			service.ws = null;
			await service.subscribe(requestedChannel);
			service.ws = { readyState: WebSocket.OPEN, send };
			service.handleMessage({
				type: "connected",
				channels: [implicitChannel],
				userId: "server-provided",
			});

			expect(send).toHaveBeenCalledWith(
				JSON.stringify({ type: "subscribe", channels: [requestedChannel] }),
			);
			expect(send).not.toHaveBeenCalledWith(
				JSON.stringify({ type: "unsubscribe", channel: implicitChannel }),
			);
		} finally {
			service.handleMessage({ type: "subscribed", channel: requestedChannel });
			await service.unsubscribe(requestedChannel);
			service.handleMessage({ type: "unsubscribed", channel: requestedChannel });
			service.ws = null;
			service.subscribedChannels.delete(implicitChannel);
		}
	});

	it("does not reconnect a channel released while disconnected", async () => {
		const service = webSocketService as unknown as {
			connect(channels: string[]): Promise<void>;
			unsubscribe(channel: string): Promise<void>;
			disconnect(): Promise<void>;
		};
		const sockets: Array<{
			url: string;
			onclose: ((event: CloseEvent) => void) | null;
		}> = [];
		const retainedChannel = "notification:retained";
		const releasedChannel = "notification:released";

		class FakeWebSocket {
			static OPEN = 1;
			readyState = FakeWebSocket.OPEN;
			onopen: ((event: Event) => void) | null = null;
			onclose: ((event: CloseEvent) => void) | null = null;
			onmessage: ((event: MessageEvent) => void) | null = null;
			onerror: ((event: Event) => void) | null = null;
			url: string;

			constructor(url: string) {
				this.url = url;
				sockets.push(this);
				queueMicrotask(() => this.onopen?.(new Event("open")));
			}

			addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
				if (type === "open") {
					queueMicrotask(() => {
						if (typeof listener === "function") {
							listener(new Event("open"));
						}
					});
				}
			}

			send() {}
			close() {}
		}

		vi.useFakeTimers();
		vi.stubGlobal("WebSocket", FakeWebSocket);
		try {
			const connect = service.connect([retainedChannel, releasedChannel]);
			await vi.runAllTicks();
			await connect;

			sockets[0].onclose?.({ code: 1006 } as CloseEvent);
			await service.unsubscribe(releasedChannel);
			await vi.advanceTimersByTimeAsync(2000);

			expect(sockets).toHaveLength(2);
			expect(sockets[1].url).toContain(
				`channels=${encodeURIComponent(retainedChannel)}`,
			);
			expect(sockets[1].url).not.toContain(
				encodeURIComponent(releasedChannel),
			);
		} finally {
			await service.disconnect();
			vi.unstubAllGlobals();
			vi.useRealTimers();
		}
	});

	it("restores a re-requested channel after its delayed unsubscribe acknowledgement", async () => {
		const service = webSocketService as unknown as {
			ws: Pick<WebSocket, "readyState" | "send"> | null;
			subscribedChannels: Set<string>;
			connect(channels: string[]): Promise<void>;
			subscribe(channel: string): Promise<void>;
			unsubscribe(channel: string): Promise<void>;
			handleMessage(message: unknown): void;
		};
		const channel = "notification:user-regression";
		const send = vi.fn();
		const sentMessages: Array<{
			type: string;
			channel?: string;
			channels?: string[];
		}> = [];
		send.mockImplementation((message: string) => {
			sentMessages.push(
				JSON.parse(message) as {
					type: string;
					channel?: string;
					channels?: string[];
				},
			);
		});
		service.ws = { readyState: WebSocket.OPEN, send };

		try {
			await service.subscribe(channel);
			service.handleMessage({ type: "subscribed", channel });

			await service.unsubscribe(channel);
			await service.connect([channel]);
			service.handleMessage({ type: "unsubscribed", channel });
		} finally {
			service.handleMessage({ type: "subscribed", channel });
			service.ws = null;
			await service.unsubscribe(channel);
			service.subscribedChannels.delete(channel);
		}

		expect(sentMessages.at(-1)).toEqual({
			type: "subscribe",
			channels: [channel],
		});
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
