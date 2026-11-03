import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { BifrostProvider } from "bifrost";

import App from "./App";
import "./index.css";

export interface BifrostAppBootstrap {
  basename: string;
  baseUrl: string;
  token: string;
  orgScope: string | null;
  appId: string | null;
  /** Owning Solution supplied by the platform host. */
  solutionId?: string | null;
  onLogout: () => void;
  theme: "light" | "dark";
}

interface BifrostAppModule {
  mount: (mountEl: HTMLElement, bootstrap: BifrostAppBootstrap) => () => void;
}

declare global {
  interface Window {
    __BIFROST_APP_MODULES__?: Map<string, BifrostAppModule>;
  }
}

export function mount(mountEl: HTMLElement, bootstrap: BifrostAppBootstrap) {
  const root = createRoot(mountEl);
  root.render(
    <StrictMode>
      <BifrostProvider
        baseUrl={bootstrap.baseUrl}
        token={bootstrap.token}
        orgScope={bootstrap.orgScope}
        appId={bootstrap.appId}
        solutionId={bootstrap.solutionId}
        theme={bootstrap.theme}
        supportsTheme
        onLogout={bootstrap.onLogout}
      >
        <BrowserRouter basename={bootstrap.basename}>
          <App />
        </BrowserRouter>
      </BifrostProvider>
    </StrictMode>,
  );
  return () => root.unmount();
}

// Register the reusable factory under this immutable entry's canonical URL.
// Child chunks import this exact URL, so the browser evaluates the graph once.
(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });

// Vite dev serves this entry directly; production mounting is owned by the host.
if (import.meta.env.DEV) {
  const mountEl = document.getElementById("root");
  if (!mountEl) throw new Error("Missing #root mount element");
  mount(mountEl, {
    basename: "/",
    baseUrl: import.meta.env.VITE_BIFROST_API_URL ?? window.location.origin,
    token: import.meta.env.VITE_BIFROST_TOKEN ?? "",
    orgScope: import.meta.env.VITE_BIFROST_ORG_ID ?? null,
    appId: import.meta.env.VITE_BIFROST_APP_ID ?? null,
    onLogout: () => window.location.assign("/login"),
    theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
  });
}
