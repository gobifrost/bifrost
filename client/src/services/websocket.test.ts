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
