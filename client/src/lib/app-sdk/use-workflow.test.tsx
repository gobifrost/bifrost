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
      return new Response(JSON.stringify({ status: "completed", result: { ok: true } }), {
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
      return new Response(JSON.stringify({ status: "completed", result: { ok: true } }), {
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
      return new Response(JSON.stringify({ status: "completed", result: { ok: true } }), {
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

  it("surfaces a workflow-level error", async () => {
    const fakeFetch = (async () =>
      new Response(JSON.stringify({ status: "failed", error: "boom" }), {
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
