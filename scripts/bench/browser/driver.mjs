import { chromium } from "playwright";

const config = JSON.parse(process.argv[2]);
const variants = config.variants.flatMap(variant => Array.from({ length: config.repetitions ?? 3 }, () => variant));
const browser = await chromium.launch({ headless: true });

async function open(variant, ordinal) {
  const context = await browser.newContext({ baseURL: config.client_url });
  const page = await context.newPage();
  await page.addInitScript(() => {
    const record = (event) => (window.__BENCH_BROWSER_EVENTS__ ??= []).push({ ...event, t_ms: performance.now() });
    const NativeWebSocket = window.WebSocket;
    class ObservedWebSocket extends NativeWebSocket {
      send(data) {
        try { const frame = JSON.parse(String(data)); const channel = frame.channels?.[0]?.name; if (frame.type === "subscribe" && channel?.startsWith("execution:")) record({ type: "subscribe", channel, execution_id: channel.slice("execution:".length) }); } catch {}
        return super.send(data);
      }
      constructor(...args) {
        super(...args);
        this.addEventListener("message", event => { try { const frame = JSON.parse(String(event.data)); if (frame.type === "execution_update" && ["Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled"].includes(frame.status)) record({ type: "terminal", execution_id: frame.executionId, status: frame.status }); } catch {} });
      }
    }
    window.WebSocket = ObservedWebSocket;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const request = args[0] instanceof Request ? args[0] : new Request(args[0], args[1]);
      const path = new URL(request.url, location.origin).pathname;
      if (request.method === "POST" && path === "/api/workflows/execute") {
        const response = await nativeFetch(...args);
        const body = await response.clone().json();
        if (body.execution_id) record({ type: "post", execution_id: body.execution_id, status: body.status, method: "POST", path });
        return response;
      }
      const response = await nativeFetch(...args);
      if (request.method === "GET" && /^\/api\/executions\/[^/]+$/.test(path)) {
        const body = await response.clone().json();
        if (typeof body.status === "string") record({ type: "get", execution_id: path.split("/").at(-1), status: body.status, method: "GET", path });
      }
      return response;
    };
  });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_PASSWORD);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await page.waitForURL(url => !url.pathname.startsWith("/login"));
  await page.goto(`/apps/${config.app_slug}?variant=${variant}`);
  return { variant, ordinal, context, page };
}

async function measure(session, stage) {
  await session.page.evaluate(() => { window.__BENCH_BROWSER_EVENTS__ = []; });
  await session.page.getByLabel("Run Browser Benchmark").click();
  await session.page.waitForFunction(() => {
    const events = window.__BENCH_BROWSER_EVENTS__ ?? [];
    if (events.some(event => event.type === "render" && event.status === "Success")) return true;
    return events.some(event => (event.type === "post" || event.type === "get" || event.type === "terminal")
      && ["Failed", "CompletedWithErrors", "Timeout", "Cancelled"].includes(event.status));
  });
  const events = await session.page.evaluate(() => window.__BENCH_BROWSER_EVENTS__);
  const failed = events.some(event => (event.type === "post" || event.type === "get" || event.type === "terminal")
    && ["Failed", "CompletedWithErrors", "Timeout", "Cancelled"].includes(event.status));
  return { variant: session.variant, ordinal: session.ordinal, stage, outcome: failed ? "terminal_failure" : "success", events };
}

const sessions = [];
try {
  sessions.push(...await Promise.all(variants.map((variant, ordinal) => open(variant, ordinal))));
  const first_load = await Promise.all(sessions.map(session => measure(session, "first_load")));
  const steady = await Promise.all(sessions.map(session => measure(session, "steady")));
  console.log(JSON.stringify({ runs: [...first_load, ...steady] }));
} finally {
  await Promise.all(sessions.map(session => session.context.close()));
  await browser.close();
}
