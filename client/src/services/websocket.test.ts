import { beforeEach, describe, expect, it, vi } from "vitest";
import { storeSelectedAuthorizationBoundary } from "@/lib/authorization-boundary";
import { EMBED_TOKEN_KEY } from "@/lib/auth-token";
import {
	type PlatformJobUpdate,
	webSocketService,
} from "./websocket";

class FakeWebSocket extends EventTarget {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSED = 3;
	static instances: FakeWebSocket[] = [];
	readonly url: string;
	readyState = FakeWebSocket.CONNECTING;
	onopen: (() => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	send = vi.fn();
	close = vi.fn((code = 1000, reason = "") => {
		this.readyState = FakeWebSocket.CLOSED;
		this.onclose?.({ code, reason } as CloseEvent);
	});

	constructor(url: string) {
		super();
		this.url = url;
		FakeWebSocket.instances.push(this);
		setTimeout(() => {
			this.readyState = FakeWebSocket.OPEN;
			this.onopen?.();
			this.dispatchEvent(new Event("open"));
		}, 0);
	}
}

function jwt(payload: Record<string, unknown>): string {
	return [
		"header",
		btoa(JSON.stringify(payload)).replace(/=/g, ""),
		"signature",
	].join(".");
}

beforeEach(async () => {
	await webSocketService.disconnect();
	FakeWebSocket.instances = [];
	sessionStorage.clear();
	localStorage.clear();
	vi.useRealTimers();
	vi.stubGlobal("WebSocket", FakeWebSocket);
});

describe("platform-job WebSocket contract", () => {
	it("dispatches the same durable snapshot by job id", () => {
		const callback = vi.fn();
		const unsubscribe = webSocketService.onPlatformJobUpdate("job-1", callback);
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
});

describe("chat WebSocket contract", () => {
	it("sends attachment ids and the governed model tier", () => {
		const send = vi.fn();
		const service = webSocketService as unknown as {
			ws: { readyState: number; send: typeof send } | null;
		};
		service.ws = { readyState: WebSocket.OPEN, send };

		expect(
			webSocketService.sendChatMessage(
				"conversation-1",
				"Summarize this",
				"local-1",
				["attachment-1"],
				"pro",
			),
		).toBe(true);
		expect(JSON.parse(send.mock.calls[0][0])).toEqual({
			type: "chat",
			conversation_id: "conversation-1",
			message: "Summarize this",
			local_id: "local-1",
			attachment_ids: ["attachment-1"],
			model_tier: "pro",
		});

		service.ws = null;
	});
});

describe("authorization-boundary WebSocket contract", () => {
	it("includes the selected user boundary on normal sockets", async () => {
		sessionStorage.setItem("userId", "user-1");
		storeSelectedAuthorizationBoundary("user-1", "platform");

		await webSocketService.connect(["platform_workers"]);

		expect(FakeWebSocket.instances).toHaveLength(1);
		const url = new URL(FakeWebSocket.instances[0].url);
		expect(url.searchParams.get("boundary")).toBe("platform");
		expect(url.searchParams.getAll("channels")).toEqual([
			"platform_workers",
		]);
	});

	it("does not attach a human boundary to embed-token sockets", async () => {
		sessionStorage.setItem("userId", "user-1");
		storeSelectedAuthorizationBoundary("user-1", "platform");
		sessionStorage.setItem(EMBED_TOKEN_KEY, jwt({ embed: true }));

		await webSocketService.connect(["execution:run-1"]);

		const url = new URL(FakeWebSocket.instances[0].url);
		expect(url.searchParams.get("boundary")).toBeNull();
		expect(url.searchParams.get("token")).toBeTruthy();
	});

	it("closes and reconnects under the new boundary when selection changes", async () => {
		sessionStorage.setItem("userId", "user-1");
		storeSelectedAuthorizationBoundary("user-1", "organization:old");
		await webSocketService.connect(["platform_workers"]);
		(
			webSocketService as unknown as {
				handleMessage(message: unknown): void;
			}
		).handleMessage({
			type: "connected",
			channels: ["platform_workers"],
			userId: "user-1",
		});

		storeSelectedAuthorizationBoundary("user-1", "platform");
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(FakeWebSocket.instances).toHaveLength(2);
		expect(FakeWebSocket.instances[0].close).toHaveBeenCalledWith(
			1000,
			"Authorization boundary changed",
		);
		const nextUrl = new URL(FakeWebSocket.instances[1].url);
		expect(nextUrl.searchParams.get("boundary")).toBe("platform");
		expect(nextUrl.searchParams.getAll("channels")).toEqual([
			"platform_workers",
		]);
	});
});
