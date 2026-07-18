import { render, screen } from "@testing-library/react";
import { StrictMode, useEffect } from "react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider, useBifrostContext } from "./provider";
import { getBifrostTransport } from "./tables";

function Probe() {
  const c = useBifrostContext();
  return (
    <span data-testid="probe">
      {c.baseUrl}|{c.token}|{c.orgScope ?? "none"}
    </span>
  );
}

describe("BifrostProvider", () => {
  it("provides baseUrl, token, and orgScope via context", () => {
    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-123" orgScope="org-9">
        <Probe />
      </BifrostProvider>,
    );
    expect(screen.getByTestId("probe").textContent).toBe(
      "https://dev.example|tok-123|org-9",
    );
  });

  it("defaults orgScope to null when omitted", () => {
    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-123">
        <Probe />
      </BifrostProvider>,
    );
    expect(screen.getByTestId("probe").textContent).toBe(
      "https://dev.example|tok-123|none",
    );
  });

  it("exposes an authed fetch that attaches the bearer token and base url", async () => {
    let captured: { url: string; auth: string | null } | null = null;
    const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      captured = { url: String(input), auth: headers.get("Authorization") };
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    function Caller() {
      const { authedFetch } = useBifrostContext();
      // fire on render
      void authedFetch("/api/workflows/run");
      return <span>called</span>;
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-abc" fetchImpl={fakeFetch}>
        <Caller />
      </BifrostProvider>,
    );
    // microtask flush
    await Promise.resolve();
    expect(captured).not.toBeNull();
    expect(captured!.url).toBe("https://dev.example/api/workflows/run");
    expect(captured!.auth).toBe("Bearer tok-abc");
  });

  it("treats / as the browser's same-origin transport", async () => {
    let captured: string | null = null;
    const fakeFetch = (async (input: RequestInfo | URL) => {
      captured = String(input);
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    function Caller() {
      const { baseUrl, authedFetch } = useBifrostContext();
      void authedFetch("/api/auth/me");
      return <span>{baseUrl || "same-origin"}</span>;
    }

    render(
      <BifrostProvider baseUrl="/" token="tok-forwarded" fetchImpl={fakeFetch}>
        <Caller />
      </BifrostProvider>,
    );
    await Promise.resolve();

    expect(screen.getByText("same-origin")).toBeInTheDocument();
    expect(captured).toBe("/api/auth/me");
    expect(getBifrostTransport().baseUrl).toBe("");
  });

  it("attaches X-Bifrost-App on authedFetch so workflow calls carry the app scope", async () => {
    // Tables/files scope via the transport's X-Bifrost-App header; workflow
    // execution goes through authedFetch. Both must carry the same context
    // signal or the server derives a different install scope per surface.
    let captured: { appHeader: string | null } | null = null;
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      captured = { appHeader: headers.get("X-Bifrost-App") };
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    function Caller() {
      const { authedFetch } = useBifrostContext();
      void authedFetch("/api/workflows/execute", { method: "POST" });
      return <span>called</span>;
    }

    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-abc"
        appId="app-777"
        fetchImpl={fakeFetch}
      >
        <Caller />
      </BifrostProvider>,
    );
    await Promise.resolve();
    expect(captured).not.toBeNull();
    expect(captured!.appHeader).toBe("app-777");
  });

  it("omits X-Bifrost-App on authedFetch when no appId is bound", async () => {
    let captured: { appHeader: string | null } | null = null;
    const fakeFetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      captured = { appHeader: headers.get("X-Bifrost-App") };
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    function Caller() {
      const { authedFetch } = useBifrostContext();
      void authedFetch("/api/workflows/execute", { method: "POST" });
      return <span>called</span>;
    }

    render(
      <BifrostProvider baseUrl="https://dev.example" token="tok-abc" fetchImpl={fakeFetch}>
        <Caller />
      </BifrostProvider>,
    );
    await Promise.resolve();
    expect(captured).not.toBeNull();
    expect(captured!.appHeader).toBeNull();
  });

  it("routes the table SDK through baseUrl + bearer while mounted", async () => {
    const { tables } = await import("./tables");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "r", data: {} }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    const { unmount } = render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-1"
        fetchImpl={fetchMock as unknown as typeof fetch}
      >
        <span>ok</span>
      </BifrostProvider>,
    );

    await tables.get("notes", "r");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toBe("https://dev.example/api/tables/notes/documents/r");

    // After unmount the transport is restored (same-origin default). The
    // restore is deferred by a microtask (StrictMode cancellation window).
    unmount();
    await Promise.resolve();
    const globalFetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "r", data: {} }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", globalFetch);
    await tables.get("notes", "r");
    expect(globalFetch.mock.calls[0][0]).toBe("/api/tables/notes/documents/r");
  });

  it("routes the file SDK through the provider app header while mounted", async () => {
    const { files } = await import("./files");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ exists: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    render(
      <BifrostProvider
        baseUrl="https://dev.example"
        token="tok-file"
        appId="app-123"
        fetchImpl={fetchMock as unknown as typeof fetch}
      >
        <span>ok</span>
      </BifrostProvider>,
    );

    await expect(files.exists("reports/q1.txt", { location: "reports" })).resolves.toBe(true);
    const [, init] = fetchMock.mock.calls[0];
    const requestHeaders = init.headers as Record<string, string>;
    expect(fetchMock.mock.calls[0][0]).toBe("https://dev.example/api/files/exists");
    expect(requestHeaders.Authorization).toBe("Bearer tok-file");
    expect(requestHeaders["X-Bifrost-App"]).toBe("app-123");
  });

  it("installs the transport before child mount effects run", () => {
    // A child's mount effect (e.g. useTable's first snapshot query) runs
    // BEFORE the provider's own useEffect. The transport must already be the
    // provider's by then, or the first query goes out same-origin unauthed.
    const seen: string[] = [];
    function TransportProbe() {
      useEffect(() => {
        seen.push(getBifrostTransport().baseUrl);
      }, []);
      return null;
    }
    render(
      <BifrostProvider baseUrl="https://remote.example" token="tok-eff">
        <TransportProbe />
      </BifrostProvider>,
    );
    expect(seen.length).toBeGreaterThan(0);
    expect(seen[0]).toBe("https://remote.example");
  });

  it("keeps the transport installed under StrictMode and restores on unmount", async () => {
    // StrictMode runs ALL passive cleanups, then re-runs effects CHILD-FIRST.
    // A synchronous restore in the provider's cleanup would expose the
    // default transport to the child's re-run mount effect — every recorded
    // value must be the provider's baseUrl, not just the post-render one.
    const seen: string[] = [];
    function TransportProbe() {
      useEffect(() => {
        seen.push(getBifrostTransport().baseUrl);
      }, []);
      return null;
    }
    const { unmount } = render(
      <StrictMode>
        <BifrostProvider baseUrl="https://strict.example" token="tok-sm">
          <TransportProbe />
        </BifrostProvider>
      </StrictMode>,
    );
    // StrictMode double-invokes the probe's mount effect (mount → cleanup →
    // mount). Both runs must see the provider transport.
    expect(seen.length).toBeGreaterThanOrEqual(2);
    expect(seen).toEqual(seen.map(() => "https://strict.example"));
    expect(getBifrostTransport().baseUrl).toBe("https://strict.example");
    unmount();
    // The restore is deferred by a microtask so StrictMode's synthetic
    // cleanup→re-setup can cancel it; flush it before asserting.
    await Promise.resolve();
    expect(getBifrostTransport().baseUrl).toBe("");
  });

  it("throws a clear error when used outside a provider", () => {
    function Orphan() {
      useBifrostContext();
      return null;
    }
    // suppress React error boundary console noise
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/BifrostProvider/);
    spy.mockRestore();
  });
});
