import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { BifrostProvider } from "./provider";
import { useWorkflow } from "./use-workflow";

vi.mock("./execution-stream", () => ({ subscribeToExecution: vi.fn() }));

import { subscribeToExecution } from "./execution-stream";

beforeEach(() => {
  (subscribeToExecution as Mock).mockReset();
});

function Runner({ onResult }: { onResult: (r: unknown) => void }) {
  const { run, loading, error } = useWorkflow<{ ok: boolean }>("my-wf");
  return (
    <div>
      <button onClick={() => run({ a: 1 }).then(onResult).catch(() => {})}>go</button>
      <span data-testid="state">{loading ? "loading" : error ? "error" : "idle"}</span>
    </div>
  );
}

/** Build a fetch mock that answers a fixed sequence of {url, response} pairs by URL match. */
function makeFetchMock(
  handlers: { match: (url: string, init?: RequestInit) => boolean; respond: () => unknown }[],
) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const handler = handlers.find((h) => h.match(url, init));
    if (!handler) throw new Error(`unhandled fetch: ${url}`);
    return new Response(JSON.stringify(handler.respond()), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  return { fetchMock, calls };
}

function isExecute(url: string) {
  return url.endsWith("/api/workflows/execute");
}
function isGetExecution(url: string, id: string) {
  return url.endsWith(`/api/executions/${id}`);
}

describe("useWorkflow", () => {
  it("POSTs /api/workflows/execute (no sync key) through the provider's authed fetch", async () => {
    const calls: { url: string; body: unknown; auth: string | null }[] = [];
    const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
        auth: headers.get("Authorization"),
      });
      return new Response(JSON.stringify({ execution_id: "e0", status: "Success", result: { ok: true } }), {
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
    expect(calls[0].body).toEqual({ workflow_id: "my-wf", input_data: { a: 1 } });
    expect("sync" in (calls[0].body as Record<string, unknown>)).toBe(false);
  });

  it("sends app_id so a path ref resolves to this install's workflow (Codex #8 P1)", async () => {
    const calls: { body: Record<string, unknown> }[] = [];
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ body: init?.body ? JSON.parse(String(init.body)) : {} });
      return new Response(JSON.stringify({ execution_id: "e0", status: "Success", result: { ok: true } }), {
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
      return new Response(JSON.stringify({ execution_id: "e0", status: "Success", result: { ok: true } }), {
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
        JSON.stringify({ execution_id: "e0", status: "Failed", error: null, result: null }),
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

    const ok = (result: string, id: string) =>
      new Response(JSON.stringify({ execution_id: id, status: "Success", result }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });

    // Newer run B settles first…
    resolvers.get("B")!(ok("B-result", "eb"));
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("B-result"));

    // …then the stale run A settles. It must not clobber B's result.
    resolvers.get("A")!(ok("A-result", "ea"));
    // flush the resolved promise chain through React
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    expect(screen.getByTestId("data").textContent).toBe("B-result");
  });

  it("surfaces a workflow-level error", async () => {
    const fakeFetch = (async () =>
      new Response(JSON.stringify({ execution_id: "e0", status: "Failed", error: "boom" }), {
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

  function StreamRunner({
    onResult,
    onError,
  }: {
    onResult: (r: unknown) => void;
    onError?: (e: Error) => void;
  }) {
    const { run, loading, error, data, logs, status, executionId } = useWorkflow<{ ok: number }>(
      "my-wf",
    );
    return (
      <div>
        <button onClick={() => run({}).then(onResult).catch((e: Error) => onError?.(e))}>go</button>
        <span data-testid="state">{loading ? "loading" : error ? "error" : "idle"}</span>
        <span data-testid="data">{data === null ? "null" : JSON.stringify(data)}</span>
        <span data-testid="status">{status ?? "null"}</span>
        <span data-testid="execId">{executionId ?? "null"}</span>
        <span data-testid="logs">{logs.map((l) => l.message).join(",")}</span>
      </div>
    );
  }

  it("executes async (no sync flag) and resolves with the fetched result on terminal event", async () => {
    let streamCb: (evt: unknown) => void = () => {};
    (subscribeToExecution as Mock).mockImplementation((_id: string, cb: (evt: unknown) => void) => {
      streamCb = cb;
      return vi.fn();
    });

    const { fetchMock } = makeFetchMock([
      { match: isExecute, respond: () => ({ execution_id: "e1", status: "Pending" }) },
      {
        match: (url) => isGetExecution(url, "e1"),
        respond: () => ({ status: "Running" }),
      },
    ]);

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fetchMock as unknown as typeof fetch}>
        <StreamRunner onResult={() => {}} />
      </BifrostProvider>,
    );

    screen.getByText("go").click();

    await waitFor(() =>
      expect(subscribeToExecution).toHaveBeenCalledWith("e1", expect.any(Function), expect.any(Function)),
    );
    await waitFor(() => expect(screen.getByTestId("execId").textContent).toBe("e1"));

    // Now change the GET handler to return the terminal result for the
    // second GET (post-terminal-event fetch).
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (isGetExecution(url, "e1")) {
        return new Response(JSON.stringify({ status: "Success", result: { ok: 1 } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`unhandled fetch: ${url}`);
    });

    act(() => {
      streamCb({ type: "status", status: "Success", isTerminal: true });
    });

    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe(JSON.stringify({ ok: 1 })));
  });

  it("streams logs into state", async () => {
    let streamCb: (evt: unknown) => void = () => {};
    (subscribeToExecution as Mock).mockImplementation((_id: string, cb: (evt: unknown) => void) => {
      streamCb = cb;
      return vi.fn();
    });

    const { fetchMock } = makeFetchMock([
      { match: isExecute, respond: () => ({ execution_id: "e1", status: "Pending" }) },
      { match: (url) => isGetExecution(url, "e1"), respond: () => ({ status: "Running" }) },
    ]);

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fetchMock as unknown as typeof fetch}>
        <StreamRunner onResult={() => {}} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();
    await waitFor(() => expect(subscribeToExecution).toHaveBeenCalled());

    act(() => {
      streamCb({ type: "log", log: { level: "info", message: "step 1", timestamp: "t1" } });
    });
    act(() => {
      streamCb({ type: "log", log: { level: "info", message: "step 2", timestamp: "t2" } });
    });

    await waitFor(() => expect(screen.getByTestId("logs").textContent).toBe("step 1,step 2"));
  });

  it("keeps the transient short-circuit (never touches the websocket)", async () => {
    const fakeFetch = (async () =>
      new Response(
        JSON.stringify({ execution_id: "e2", is_transient: true, status: "Success", result: { x: 2 } }),
        { status: 200, headers: { "content-type": "application/json" } },
      )) as typeof fetch;

    const onResult = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fakeFetch}>
        <StreamRunner onResult={onResult} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();

    await waitFor(() => expect(onResult).toHaveBeenCalledWith({ x: 2 }));
    expect(subscribeToExecution).not.toHaveBeenCalled();
  });

  it("settles from the immediate check when the run finished before the socket", async () => {
    (subscribeToExecution as Mock).mockImplementation(() => vi.fn());

    const { fetchMock } = makeFetchMock([
      { match: isExecute, respond: () => ({ execution_id: "e3", status: "Pending" }) },
      {
        match: (url) => isGetExecution(url, "e3"),
        respond: () => ({ status: "Success", result: { fast: true } }),
      },
    ]);

    const onResult = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-x" fetchImpl={fetchMock as unknown as typeof fetch}>
        <StreamRunner onResult={onResult} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();

    await waitFor(() => expect(onResult).toHaveBeenCalledWith({ fast: true }));
  });

  it("falls back to polling when the socket drops", async () => {
    vi.useFakeTimers();
    let onSocketDown: (() => void) | undefined;
    (subscribeToExecution as Mock).mockImplementation(
      (_id: string, _cb: (evt: unknown) => void, down?: () => void) => {
        onSocketDown = down;
        return vi.fn();
      },
    );

    let pollCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (isExecute(url)) {
        return new Response(JSON.stringify({ execution_id: "e4", status: "Pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (isGetExecution(url, "e4")) {
        pollCount += 1;
        // First call is the immediate post-subscribe check (Running).
        // Subsequent calls are polls; the 2nd poll onward returns Success.
        const status = pollCount <= 2 ? "Running" : "Success";
        const body =
          status === "Success"
            ? { status: "Success", result: { polled: true } }
            : { status: "Running" };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`unhandled fetch: ${url}`);
    });

    const onResult = vi.fn();
    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-x"
        fetchImpl={fetchMock as unknown as typeof fetch}
      >
        <StreamRunner onResult={onResult} />
      </BifrostProvider>,
    );
    await act(async () => {
      screen.getByText("go").click();
    });

    await vi.waitFor(() => expect(onSocketDown).toBeDefined());
    act(() => {
      onSocketDown!();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(onResult).toHaveBeenCalledWith({ polled: true });
    vi.useRealTimers();
  });

  it("rejects with error_message on Failed terminal", async () => {
    let streamCb: (evt: unknown) => void = () => {};
    (subscribeToExecution as Mock).mockImplementation((_id: string, cb: (evt: unknown) => void) => {
      streamCb = cb;
      return vi.fn();
    });

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (isExecute(url)) {
        return new Response(JSON.stringify({ execution_id: "e5", status: "Pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (isGetExecution(url, "e5")) {
        return new Response(JSON.stringify({ status: "Failed", error_message: "boom" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`unhandled fetch: ${url}`);
    });

    const rejections: Error[] = [];
    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-x"
        fetchImpl={fetchMock as unknown as typeof fetch}
      >
        <StreamRunner onResult={() => {}} onError={(e) => rejections.push(e)} />
      </BifrostProvider>,
    );
    screen.getByText("go").click();
    await waitFor(() => expect(subscribeToExecution).toHaveBeenCalled());

    act(() => {
      streamCb({ type: "status", status: "Failed", isTerminal: true });
    });

    await waitFor(() => expect(rejections).toHaveLength(1));
    expect(rejections[0].message).toBe("boom");
  });
});
