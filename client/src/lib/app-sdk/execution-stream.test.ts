import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { subscribeToExecution } from "./execution-stream";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  sent: string[] = [];
  listeners: Record<string, ((e: any) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  addEventListener(type: string, cb: (e: any) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.emit("close", { code: 1000 });
  }
  emit(type: string, e: any) {
    (this.listeners[type] ?? []).forEach((cb) => cb(e));
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});
afterEach(() => vi.unstubAllGlobals());

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
    const events: any[] = [];
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
    const events: any[] = [];
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
    subscribeToExecution("abc-123", () => {}, down);
    const ws = MockWebSocket.instances[0];
    ws.emit("error", {});
    ws.emit("close", { code: 1000 });
    expect(down).toHaveBeenCalledTimes(1);
  });

  it("ignores unparseable frames", () => {
    const events: any[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("message", { data: "not json" });
    expect(events).toEqual([]);
  });
});
