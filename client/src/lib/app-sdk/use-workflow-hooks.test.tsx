import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "./provider";
import { useWorkflowMutation, useWorkflowQuery } from "./use-workflow-hooks";

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
});

function QueryWithParams() {
  const { data } = useWorkflowQuery<{ ok: boolean }>("wf::q", { q: "x" });
  return <span>{data ? "done" : "..."}</span>;
}

describe("useWorkflowMutation (imperative, React-Query-shaped)", () => {
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
