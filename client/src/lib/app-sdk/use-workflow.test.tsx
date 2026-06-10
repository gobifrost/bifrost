import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "./provider";
import { useWorkflow } from "./use-workflow";

function Runner({ onResult }: { onResult: (r: unknown) => void }) {
  const { run, loading, error } = useWorkflow<{ ok: boolean }>("my-wf");
  return (
    <div>
      <button onClick={() => run({ a: 1 }).then(onResult).catch(() => {})}>go</button>
      <span data-testid="state">{loading ? "loading" : error ? "error" : "idle"}</span>
    </div>
  );
}

describe("useWorkflow", () => {
  it("POSTs /api/workflows/execute through the provider's authed fetch", async () => {
    const calls: { url: string; body: unknown; auth: string | null }[] = [];
    const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
        auth: headers.get("Authorization"),
      });
      return new Response(JSON.stringify({ status: "Success", result: { ok: true } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const onResult = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <Runner onResult={onResult} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();

    await waitFor(() => expect(onResult).toHaveBeenCalledWith({ ok: true }));
    expect(calls[0].url).toBe("https://dev.example/api/workflows/execute");
    expect(calls[0].auth).toBe("Bearer tok-x");
    expect(calls[0].body).toEqual({ workflow_id: "my-wf", input_data: { a: 1 }, sync: true });
  });

  it("sends app_id so a path ref resolves to this install's workflow (Codex #8 P1)", async () => {
    const calls: { body: Record<string, unknown> }[] = [];
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ body: init?.body ? JSON.parse(String(init.body)) : {} });
      return new Response(JSON.stringify({ status: "Success", result: { ok: true } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-x"
        appId="app-123"
        fetchImpl={fakeFetch}
      >
        <Runner onResult={() => {}} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].body.app_id).toBe("app-123");
  });

  it("omits app_id when the host supplies none (dev / non-solution app)", async () => {
    const calls: { body: Record<string, unknown> }[] = [];
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ body: init?.body ? JSON.parse(String(init.body)) : {} });
      return new Response(JSON.stringify({ status: "Success", result: { ok: true } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <Runner onResult={() => {}} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();
    await waitFor(() => expect(calls.length).toBe(1));
    expect("app_id" in calls[0].body).toBe(false);
  });

  it("rejects on status=Failed even when error is null, leaving data unchanged", async () => {
    // "Failed" is the real wire value — ExecutionStatus is PascalCase.
    const fakeFetch = (async () =>
      new Response(
        JSON.stringify({ status: "Failed", error: null, result: null }),
        { status: 200, headers: { "content-type": "application/json" } },
      )) as typeof fetch;

    const onResult = vi.fn();
    const rejections: Error[] = [];
    function FailureRunner() {
      const { run, data, loading, error } = useWorkflow<{ ok: boolean }>("my-wf");
      return (
        <div>
          <button
            onClick={() =>
              run({})
                .then(onResult)
                .catch((e: Error) => rejections.push(e))
            }
          >
            go
          </button>
          <span data-testid="state">{loading ? "loading" : error ? "error" : "idle"}</span>
          <span data-testid="data">{data === null ? "null" : JSON.stringify(data)}</span>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <FailureRunner />
      </BifrostProvider>,
    );
    screen.getByText("go").click();

    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("error"));
    expect(onResult).not.toHaveBeenCalled();
    expect(rejections).toHaveLength(1);
    expect(rejections[0].message).toMatch(/failed/);
    // data must NOT be set to the failed run's null result
    expect(screen.getByTestId("data").textContent).toBe("null");
  });

  it("a slow stale run cannot overwrite a newer run's result", async () => {
    // Two overlapping runs: A (started first, resolves last) and B. After
    // both settle, data must be B's result and loading must be false.
    const resolvers = new Map<string, (r: Response) => void>();
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as {
        input_data: { which: string };
      };
      return new Promise<Response>((resolve) => {
        resolvers.set(body.input_data.which, resolve);
      });
    }) as typeof fetch;

    function SequenceRunner() {
      const { run, data, loading } = useWorkflow<string>("my-wf");
      return (
        <div>
          <button onClick={() => void run({ which: "A" }).catch(() => {})}>runA</button>
          <button onClick={() => void run({ which: "B" }).catch(() => {})}>runB</button>
          <span data-testid="data">{data ?? "none"}</span>
          <span data-testid="loading">{String(loading)}</span>
        </div>
      );
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <SequenceRunner />
      </BifrostProvider>,
    );

    screen.getByText("runA").click();
    screen.getByText("runB").click();
    await waitFor(() => expect(resolvers.size).toBe(2));

    const ok = (result: string) =>
      new Response(JSON.stringify({ status: "Success", result }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });

    // Newer run B settles first…
    resolvers.get("B")!(ok("B-result"));
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("B-result"));

    // …then the stale run A settles. It must not clobber B's result.
    resolvers.get("A")!(ok("A-result"));
    // flush the resolved promise chain through React
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    expect(screen.getByTestId("data").textContent).toBe("B-result");
  });

  it("surfaces a workflow-level error", async () => {
    const fakeFetch = (async () =>
      new Response(JSON.stringify({ status: "Failed", error: "boom" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })) as typeof fetch;

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <Runner onResult={() => {}} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("error"));
  });
});
