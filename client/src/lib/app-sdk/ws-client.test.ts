import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setBifrostTransport } from "./tables";
import { buildWsUrl, subscribeToTable } from "./ws-client";

type MockEvent = { data?: string; code?: number };

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  sent: string[] = [];
  listeners: Record<string, ((event: MockEvent) => void)[]> = {};

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, callback: (event: MockEvent) => void) {
    (this.listeners[type] ??= []).push(callback);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.emit("close", { code: 1000 });
  }

  emit(type: string, event: MockEvent) {
    for (const callback of this.listeners[type] ?? []) callback(event);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("subscribeToTable", () => {
  it("reconnects, resubscribes, and signals snapshot recovery after acknowledgement", () => {
    const events: Record<string, unknown>[] = [];
    const recovered = vi.fn();
    const unsubscribe = subscribeToTable(
      "table-1",
      { eq: [{ row: "status" }, "active"] },
      (event) => events.push(event),
      recovered,
    );

    const first = MockWebSocket.instances[0];
    first.emit("open", {});
    first.emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "table:table-1" }),
    });
    expect(recovered).not.toHaveBeenCalled();

    first.emit("close", { code: 1012 });
    vi.advanceTimersByTime(500);

    const second = MockWebSocket.instances[1];
    second.emit("open", {});
    expect(JSON.parse(second.sent[0])).toEqual({
      type: "subscribe",
      channels: [
        {
          name: "table:table-1",
          filter: { eq: [{ row: "status" }, "active"] },
        },
      ],
    });
    expect(recovered).not.toHaveBeenCalled();

    second.emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "table:table-1" }),
    });
    expect(recovered).toHaveBeenCalledTimes(1);
    expect(events).toHaveLength(2);
    unsubscribe();
  });

  it("cancels a scheduled reconnect when the caller unsubscribes", () => {
    const unsubscribe = subscribeToTable("table-1", null, () => {});
    MockWebSocket.instances[0].emit("close", { code: 1006 });

    unsubscribe();
    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("requests a snapshot refresh when the initial handshake only succeeds after retry", () => {
    const recovered = vi.fn();
    const unsubscribe = subscribeToTable("table-1", null, () => {}, recovered);
    MockWebSocket.instances[0].emit("close", { code: 1006 });

    vi.advanceTimersByTime(500);
    MockWebSocket.instances[1].emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "table:table-1" }),
    });

    expect(recovered).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("treats an authorization close as terminal", () => {
    const unsubscribe = subscribeToTable("table-1", null, () => {});
    MockWebSocket.instances[0].emit("close", { code: 4003 });

    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
    unsubscribe();
  });
});

describe("buildWsUrl", () => {
  let restore: (() => void) | null = null;

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it("targets the transport baseUrl with token auth (npm-dev / solution start)", () => {
    restore = setBifrostTransport({
      baseUrl: "https://remote.example",
      token: "tok",
    });
    expect(buildWsUrl()).toBe("wss://remote.example/ws/connect?token=tok");
  });

  it("defaults to the window origin with NO token param (v1 inline, cookie auth)", () => {
    const url = new URL(buildWsUrl());
    const origin = new URL(window.location.href);
    expect(url.protocol).toBe(origin.protocol === "https:" ? "wss:" : "ws:");
    expect(url.host).toBe(origin.host);
    expect(url.pathname).toBe("/ws/connect");
    expect(url.searchParams.has("token")).toBe(false);
  });

  it("maps an http baseUrl to the ws: scheme", () => {
    restore = setBifrostTransport({
      baseUrl: "http://localhost:8000",
      token: "t2",
    });
    expect(buildWsUrl()).toBe("ws://localhost:8000/ws/connect?token=t2");
  });
});
