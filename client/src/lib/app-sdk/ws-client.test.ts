import { afterEach, describe, expect, it } from "vitest";

import { setBifrostTransport } from "./tables";
import { buildWsUrl } from "./ws-client";

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
