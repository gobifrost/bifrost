/**
 * Return a self-contained init script so the page observer can run before the
 * application bundle without changing any request body.
 */
export function browserObserverScript() {
  return `(() => {
    const record = (event) => (window.__BENCH_BROWSER_EVENTS__ ??= []).push({ ...event, t_ms: performance.now() });
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args);
      try {
        const input = args[0];
        const init = args[1];
        const request = input instanceof Request ? input : null;
        const method = String(init?.method ?? request?.method ?? "GET").toUpperCase();
        const rawUrl = request?.url ?? String(input);
        const path = new URL(rawUrl, window.document.baseURI).pathname;
        if (method === "POST" && path === "/api/workflows/execute") {
          const body = await response.clone().json();
          if (body.execution_id) record({ type: "post", execution_id: body.execution_id, status: body.status, method, path });
        }
        if (method === "GET" && /^\\/api\\/executions\\/[^/]+$/.test(path)) {
          const body = await response.clone().json();
          if (typeof body.status === "string") record({ type: "get", execution_id: path.split("/").at(-1), status: body.status, method, path });
        }
      } catch {
        // Observation must not alter the application's successful response.
      }
      return response;
    };
  })();`;
}
