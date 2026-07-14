import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

import { BifrostProvider } from "./provider";
import { useWorkflowMutation, useWorkflowQuery } from "./use-workflow-hooks";
import * as useWorkflowModule from "./use-workflow";

afterEach(() => {
  vi.restoreAllMocks();
});

function fakeFetchReturning(result: unknown, record?: (body: unknown) => void) {
  return (async (_input: RequestInfo | URL, init?: RequestInit) => {
    record?.(init?.body ? JSON.parse(String(init.body)) : null);
    return new Response(JSON.stringify({ status: "Success", result }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

describe("useWorkflowQuery (auto-running, React-Query-shaped)", () => {
  it("exposes logs, status, and executionId from the underlying useWorkflow", async () => {
    const mockLogs = [
      { level: "info", message: "Started", timestamp: "2026-07-14T00:00:00Z", sequence: 1 },
      { level: "debug", message: "Processing", timestamp: "2026-07-14T00:00:01Z", sequence: 2 },
    ];
    const mockStatus = "Running";
    const mockExecutionId = "exec-123";

    vi.spyOn(useWorkflowModule, "useWorkflow").mockReturnValue({
      data: { result: "test-result" },
      loading: false,
      error: null,
      run: vi.fn(async () => ({ result: "test-result" })),
      logs: mockLogs,
      status: mockStatus,
      executionId: mockExecutionId,
    });

    function View() {
      const { logs, status, executionId } = useWorkflowQuery<{ result: string }>("wf::test");
      return (
        <div>
          <span data-testid="logs">{logs.length}</span>
          <span data-testid="status">{status}</span>
          <span data-testid="executionId">{executionId}</span>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fakeFetchReturning({})}>
        <View />
      </BifrostProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("logs").textContent).toBe("2");
      expect(screen.getByTestId("status").textContent).toBe("Running");
      expect(screen.getByTestId("executionId").textContent).toBe("exec-123");
    });
  });

  it("auto-runs on mount and exposes data + refresh", async () => {
    let runs = 0;
    const fetchImpl = (async () => {
      runs += 1;
      return new Response(
        JSON.stringify({ status: "Success", result: { n: runs } }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }) as typeof fetch;

    function View() {
      const { data, loading, refresh } = useWorkflowQuery<{ n: number }>("wf::q");
      return (
        <div>
          <span data-testid="d">{data ? `n=${data.n}` : loading ? "loading" : "empty"}</span>
          <button onClick={() => refresh()}>refresh</button>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl}>
        <View />
      </BifrostProvider>,
    );
    // Auto-ran on mount — the LLM-trap of "data is null because I forgot to run" is gone.
    await waitFor(() => expect(screen.getByTestId("d").textContent).toBe("n=1"));
    screen.getByText("refresh").click();
    await waitFor(() => expect(screen.getByTestId("d").textContent).toBe("n=2"));
  });

  it("passes input_data from the params arg on the initial run", async () => {
    const bodies: unknown[] = [];
    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="t"
        fetchImpl={fakeFetchReturning({ ok: true }, (b) => bodies.push(b))}
      >
        <QueryWithParams />
      </BifrostProvider>,
    );
    await waitFor(() => expect(bodies.length).toBeGreaterThan(0));
    const body = bodies[0] as { workflow_id: string; input_data: Record<string, unknown> };
    expect(body.workflow_id).toBe("wf::q");
    expect(body.input_data).toEqual({ q: "x" });
  });

  it("refresh() with no args re-runs with the ORIGINAL params, not {}", async () => {
    const bodies: Array<{ input_data: Record<string, unknown> }> = [];
    function View() {
      const { refresh } = useWorkflowQuery<{ ok: boolean }>("wf::q", { q: "x" });
      return <button onClick={() => refresh()}>refresh</button>;
    }
    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="t"
        fetchImpl={fakeFetchReturning({ ok: true }, (b) =>
          bodies.push(b as { input_data: Record<string, unknown> }),
        )}
      >
        <View />
      </BifrostProvider>,
    );
    await waitFor(() => expect(bodies.length).toBe(1)); // auto-run
    screen.getByText("refresh").click();
    await waitFor(() => expect(bodies.length).toBe(2));
    // The refresh kept {q:"x"} — a bare refresh button reloads the same data.
    expect(bodies[1].input_data).toEqual({ q: "x" });
  });
});

function QueryWithParams() {
  const { data } = useWorkflowQuery<{ ok: boolean }>("wf::q", { q: "x" });
  return <span>{data ? "done" : "..."}</span>;
}

describe("useWorkflowMutation (imperative, React-Query-shaped)", () => {
  it("exposes logs, status, and executionId from the underlying useWorkflow", async () => {
    const mockLogs = [
      { level: "info", message: "Mutation started", timestamp: "2026-07-14T00:00:00Z", sequence: 1 },
    ];
    const mockStatus = "Success";
    const mockExecutionId = "exec-456";

    vi.spyOn(useWorkflowModule, "useWorkflow").mockReturnValue({
      data: { done: true },
      loading: false,
      error: null,
      run: vi.fn(async () => ({ done: true })),
      logs: mockLogs,
      status: mockStatus,
      executionId: mockExecutionId,
    });

    function View() {
      const { logs, status, executionId } = useWorkflowMutation<{ done: boolean }>("wf::test");
      return (
        <div>
          <span data-testid="logs">{logs.length}</span>
          <span data-testid="status">{status}</span>
          <span data-testid="executionId">{executionId}</span>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fakeFetchReturning({})}>
        <View />
      </BifrostProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("logs").textContent).toBe("1");
      expect(screen.getByTestId("status").textContent).toBe("Success");
      expect(screen.getByTestId("executionId").textContent).toBe("exec-456");
    });
  });

  it("does NOT auto-run; mutate() triggers and returns the result", async () => {
    let runs = 0;
    const fetchImpl = (async () => {
      runs += 1;
      return new Response(JSON.stringify({ status: "Success", result: { done: true } }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const onDone = vi.fn();
    function View() {
      const { mutate, loading } = useWorkflowMutation<{ done: boolean }>("wf::m");
      return (
        <div>
          <span data-testid="s">{loading ? "loading" : "idle"}</span>
          <button onClick={() => mutate({ a: 1 }).then(onDone).catch(() => {})}>go</button>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl}>
        <View />
      </BifrostProvider>,
    );
    // Imperative: nothing ran on mount.
    expect(runs).toBe(0);
    screen.getByText("go").click();
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ done: true }));
    expect(runs).toBe(1);
  });
});
