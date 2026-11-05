import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BifrostProvider, useBifrostContext } from "bifrost";

function App() {
  const { authedFetch, theme } = useBifrostContext();
  const [connection, setConnection] = useState("Connecting");

  useEffect(() => {
    let active = true;
    void authedFetch("/api/auth/me").then(
      (response) => {
        if (active) setConnection(response.ok ? "Connected" : "Connection failed");
      },
      () => {
        if (active) setConnection("Connection failed");
      },
    );
    return () => {
      active = false;
    };
  }, [authedFetch]);

  return <main><h1>Hosted SDK Content</h1><output aria-label="SDK Theme">{theme}</output><output aria-label="SDK Connection">{connection}</output></main>;
}

export function mount(element: HTMLElement, bootstrap: Record<string, unknown>) {
  const root = createRoot(element);
  root.render(<BifrostProvider {...bootstrap} baseUrl={String(bootstrap.baseUrl)} token={String(bootstrap.token)} theme="light"><App /></BifrostProvider>);
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
