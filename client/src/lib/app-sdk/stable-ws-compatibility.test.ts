import { afterEach, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import ts from "typescript";
import stableSource from "./fixtures/stable-1.4.1-ws-client.ts.txt?raw";
import provenance from "./fixtures/stable-1.4.1-ws-client.ts.provenance.json";
import { webSocketService } from "@/services/websocket";
import { ACCESS_TOKEN_KEY, getActiveToken } from "@/lib/auth-token";
import "@/lib/api-client";

type Frame = { data?: string; code?: number };
class Socket {
	static readonly OPEN = 1;
	static all: Socket[] = [];
	readyState = Socket.OPEN;
	onopen: (() => void) | null = null;
	onclose: ((e: Frame) => void) | null = null;
	onmessage: ((e: Frame) => void) | null = null;
	sent: string[] = [];
	listeners: Record<string, ((e: Frame) => void)[]> = {};
	constructor(public url: string) {
		Socket.all.push(this);
		queueMicrotask(() => this.emit("open"));
	}
	addEventListener(name: string, callback: (e: Frame) => void) {
		(this.listeners[name] ??= []).push(callback);
	}
	emit(name: string, event: Frame = {}) {
		if (name === "open") this.onopen?.();
		if (name === "close") this.onclose?.(event);
		if (name === "message") this.onmessage?.(event);
		this.listeners[name]?.forEach((callback) => callback(event));
	}
	send(data: string) { this.sent.push(data); }
	close() { this.emit("close", { code: 1000 }); }
}

const previousLocks = Object.getOwnPropertyDescriptor(navigator, "locks");
afterEach(async () => {
	await webSocketService.disconnect();
	if (previousLocks) Object.defineProperty(navigator, "locks", previousLocks);
	else delete (navigator as unknown as { locks?: unknown }).locks;
	vi.useRealTimers();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
	localStorage.clear();
});

it("the immutable1.4.1 SDK resubscribes with the host's refreshed token after1012", async () => {
	expect(createHash("sha256").update(stableSource).digest("hex")).toBe(provenance.sha256);
	vi.useFakeTimers();
	vi.spyOn(Math, "random").mockReturnValue(0.5);
	vi.stubGlobal("WebSocket", Socket);
	Socket.all = [];
	localStorage.setItem(ACCESS_TOKEN_KEY, "old-access");
	Object.defineProperty(navigator, "locks", {
		configurable: true,
		value: { request: async (_name: string, callback: () => Promise<boolean>) => callback() },
	});
	const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ access_token: "fresh-access" }), {
		status: 200, headers: { "Content-Type": "application/json" },
	}));
	vi.stubGlobal("fetch", fetch);
	// Transpile the frozen source without changing its URL, terminal-close or
	// retry logic. Supply only the original provider's live token transport.
	const output = ts.transpileModule(stableSource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
	const exports: { subscribeToTable?: (id: string, filter: null, event: () => void, reconnect: () => void) => () => void } = {};
	new Function("require", "exports", output)(() => ({
		getBifrostTransport: () => ({ baseUrl: window.location.origin, getToken: getActiveToken }),
	}), exports);
	const connecting = webSocketService.connect();
	await vi.runAllTicks();
	await connecting;
	const restored = vi.fn();
	const stop = exports.subscribeToTable!("stable-table", null, () => {}, restored);
	await vi.runAllTicks();
	const host = Socket.all[0];
	const legacy = Socket.all[1];
	expect(new URL(host.url).searchParams.has("token")).toBe(false);
	expect(new URL(legacy.url).searchParams.get("token")).toBe("old-access");
	legacy.emit("message", { data: JSON.stringify({ type: "subscribed", channel: "table:stable-table" }) });
	// The server denies the stale operation and routes a private refresh
	// request to the host. These are the resulting distinct close codes.
	legacy.emit("close", { code: 1012 });
	host.emit("close", { code: 4001 });
	await vi.advanceTimersByTimeAsync(500);
	const earlyRetry = Socket.all[2];
	expect(new URL(earlyRetry.url).searchParams.get("token")).toBe("old-access");
	earlyRetry.emit("close", { code: 1012 });
	await vi.advanceTimersByTimeAsync(1_000);
	Socket.all[3].emit("close", { code: 1012 });
	await vi.advanceTimersByTimeAsync(500);
	expect(fetch).toHaveBeenCalledTimes(1);
	expect(fetch.mock.calls[0][0]).toBe("/api/auth/refresh");
	expect(getActiveToken()).toBe("fresh-access");
	await vi.advanceTimersByTimeAsync(2_000);
	const recovered = Socket.all.find((s) => new URL(s.url).searchParams.get("token") === "fresh-access");
	expect(recovered).toBeDefined();
	expect(JSON.parse(recovered!.sent[0])).toMatchObject({ type: "subscribe", channels: [{ name: "table:stable-table" }] });
	recovered!.emit("message", { data: JSON.stringify({ type: "subscribed", channel: "table:stable-table" }) });
	expect(restored).toHaveBeenCalledTimes(1);
	stop();
});
