import { createRoot } from "react-dom/client";
import { BifrostProvider, useBifrostContext } from "bifrost";

function App() {
  const context = useBifrostContext();
  return <main><h1>Hosted SDK Content</h1><output aria-label="SDK Theme">{context.theme}</output></main>;
}

export function mount(element: HTMLElement, bootstrap: Record<string, unknown>) {
  const root = createRoot(element);
  root.render(<BifrostProvider {...bootstrap} baseUrl={String(bootstrap.baseUrl)} token={String(bootstrap.token)} theme="light"><App /></BifrostProvider>);
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
