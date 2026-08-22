import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { subscribeToExecution } from "./execution-stream";
import type { ExecutionStreamEvent } from "./execution-stream";

type MockEvent = { data?: string; code?: number };

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  sent: string[] = [];
  listeners: Record<string, ((e: MockEvent) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  addEventListener(type: string, cb: (e: MockEvent) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.emit("close", { code: 1000 });
  }
  emit(type: string, e: MockEvent) {
    (this.listeners[type] ?? []).forEach((cb) => cb(e));
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("subscribeToExecution", () => {
  it("subscribes to the execution channel on open", () => {
    subscribeToExecution("abc-123", () => {});
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    expect(JSON.parse(ws.sent[0])).toEqual({
      type: "subscribe",
      channels: [{ name: "execution:abc-123" }],
    });
  });

  it("maps execution_update frames to status events with terminal detection", () => {
    const events: ExecutionStreamEvent[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    ws.emit("message", {
      data: JSON.stringify({
        type: "execution_update",
        executionId: "abc-123",
        status: "Running",
      }),
    });
    ws.emit("message", {
      data: JSON.stringify({
        type: "execution_update",
        executionId: "abc-123",
        status: "Success",
        duration_ms: 42,
      }),
    });
    expect(events).toEqual([
      { type: "status", status: "Running", isTerminal: false },
      { type: "status", status: "Success", isTerminal: true },
    ]);
  });

  it("maps execution_log frames to log events", () => {
    const events: ExecutionStreamEvent[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    ws.emit("message", {
      data: JSON.stringify({
        type: "execution_log",
        executionId: "abc-123",
        level: "info",
        message: "hi",
        timestamp: "t",
        sequence: 3,
      }),
    });
    expect(events[0]).toEqual({
      type: "log",
      log: { level: "info", message: "hi", timestamp: "t", sequence: 3 },
    });
  });

  it("emits ready only after the server acknowledges the execution subscription", () => {
    const events: ExecutionStreamEvent[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    expect(events).toEqual([]);
    ws.emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "execution:abc-123" }),
    });
    expect(events).toEqual([{ type: "ready" }]);
  });

  it("falls back when the server rejects or revokes the subscription", () => {
    const rejected = vi.fn();
    subscribeToExecution("abc-123", () => {}, rejected);
    MockWebSocket.instances[0].emit("message", {
      data: JSON.stringify({
        type: "error",
        channel: "execution:abc-123",
        message: "Access denied",
      }),
    });
    expect(rejected).toHaveBeenCalledTimes(1);

    const revoked = vi.fn();
    subscribeToExecution("def-456", () => {}, revoked);
    MockWebSocket.instances[1].emit("message", {
      data: JSON.stringify({
        type: "subscription_revoked",
        channel: "execution:def-456",
      }),
    });
    expect(revoked).toHaveBeenCalledTimes(1);
  });

  it("fires onSocketDown on error and on unexpected close, but not on unsubscribe", () => {
    const down = vi.fn();
    const unsub = subscribeToExecution("abc-123", () => {}, down);
    const ws = MockWebSocket.instances[0];
    ws.emit("error", {});
    expect(down).toHaveBeenCalledTimes(1);
    unsub(); // client-initiated close must NOT fire onSocketDown again
    expect(down).toHaveBeenCalledTimes(1);
  });

  it("dedupes onSocketDown when error is followed by close", () => {
    const down = vi.fn();
    const unsubscribe = subscribeToExecution("abc-123", () => {}, down);
    const ws = MockWebSocket.instances[0];
    ws.emit("error", {});
    ws.emit("close", { code: 1000 });
    expect(down).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("reconnects after a deployment close and re-emits ready after acknowledgement", () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const events: ExecutionStreamEvent[] = [];
    const down = vi.fn();
    const unsubscribe = subscribeToExecution(
      "abc-123",
      (event) => events.push(event),
      down,
    );

    const first = MockWebSocket.instances[0];
    first.emit("open", {});
    first.emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "execution:abc-123" }),
    });
    first.emit("close", { code: 1012 });

    expect(down).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(500);

    const second = MockWebSocket.instances[1];
    second.emit("open", {});
    second.emit("message", {
      data: JSON.stringify({ type: "subscribed", channel: "execution:abc-123" }),
    });

    expect(JSON.parse(second.sent[0])).toEqual({
      type: "subscribe",
      channels: [{ name: "execution:abc-123" }],
    });
    expect(events).toEqual([{ type: "ready" }, { type: "ready" }]);
    unsubscribe();
  });

  it("does not retry authentication and policy close codes", () => {
    vi.useFakeTimers();
    const unsubscribe = subscribeToExecution("abc-123", () => {});
    MockWebSocket.instances[0].emit("close", { code: 4001 });

    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    unsubscribe();
  });

  it("ignores unparseable frames", () => {
    const events: ExecutionStreamEvent[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("message", { data: "not json" });
    expect(events).toEqual([]);
  });
});
