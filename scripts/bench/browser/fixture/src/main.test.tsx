import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const workflow = vi.hoisted(() => ({
  state: {
    data: null as Record<string, unknown> | null,
    error: null as Error | null,
    executionId: null as string | null,
    status: null as string | null,
    run: vi.fn(),
  },
}));

vi.mock("bifrost", () => ({
  BifrostProvider: ({ children }: { children: ReactNode }) => children,
  useWorkflow: () => workflow.state,
}));

import { BenchApp } from "./main";

describe("BenchApp render marker", () => {
  let frames: FrameRequestCallback[];

  beforeEach(() => {
    frames = [];
    workflow.state = {
      data: null,
      error: null,
      executionId: null,
      status: null,
      run: vi.fn(),
    };
    window.__BENCH_BROWSER_EVENTS__ = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("waits for the matching result to render after a terminal status frame", () => {
    const view = render(<BenchApp />);
    fireEvent.click(screen.getByRole("button", { name: "Run Browser Benchmark" }));
    expect(workflow.state.run).toHaveBeenCalledWith({ bench_request_id: "request-1" });

    workflow.state = { ...workflow.state, executionId: "execution-1", status: "Success" };
    view.rerender(<BenchApp />);
    expect(frames).toEqual([]);

    workflow.state = { ...workflow.state, data: { request_id: "other-request" } };
    view.rerender(<BenchApp />);
    expect(frames).toEqual([]);

    workflow.state = { ...workflow.state, data: { request_id: "request-1", records: ["visible"] } };
    view.rerender(<BenchApp />);
    expect(screen.getByRole("status").textContent).toContain("request-1");
    expect(frames).toHaveLength(1);
    act(() => frames[0](0));
    expect(window.__BENCH_BROWSER_EVENTS__).toContainEqual({
      type: "render", execution_id: "execution-1", status: "Success", t_ms: expect.any(Number),
    });
  });

  it("waits for the terminal error to become visible before marking a failure", () => {
    const view = render(<BenchApp />);
    fireEvent.click(screen.getByRole("button", { name: "Run Browser Benchmark" }));

    workflow.state = { ...workflow.state, executionId: "execution-2", status: "Failed" };
    view.rerender(<BenchApp />);
    expect(frames).toEqual([]);

    workflow.state = { ...workflow.state, error: new Error("workflow failed") };
    view.rerender(<BenchApp />);
    expect(screen.getByRole("status").textContent).toContain("workflow failed");
    expect(frames).toHaveLength(1);
    act(() => frames[0](0));
    expect(window.__BENCH_BROWSER_EVENTS__).toContainEqual({
      type: "render", execution_id: "execution-2", status: "Failed", t_ms: expect.any(Number),
    });
  });
});
