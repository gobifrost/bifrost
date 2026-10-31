import { chromium } from "playwright";
import { browserObserverScript } from "./observer.mjs";

const config = JSON.parse(process.argv[2]);
const batches = config.batches;
if (!Array.isArray(batches) || !batches.length || batches.some(batch => !Array.isArray(batch) || batch.length !== 3)) {
  throw new Error("browser benchmark needs nonempty three-page observation batches");
}
const browser = await chromium.launch({ headless: true });
const AUTH_PATHS = new Set(["/auth/status", "/auth/login", "/auth/me", "/auth/refresh", "/api/auth/refresh"]);

async function open({ variant, ordinal }) {
  // The preparation receipt records the one-day benchmark certificate. The
  // observer must opt in explicitly; it never weakens ordinary SDK transport.
  const context = await browser.newContext({ baseURL: config.client_url, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  await page.addInitScript({ content: browserObserverScript() });
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
  });
  const authResponses = [];
  const clientOrigin = new URL(config.client_url).origin;
  const onResponse = response => {
    try {
      const url = new URL(response.url());
      if (url.origin === clientOrigin && AUTH_PATHS.has(url.pathname)) {
        const observed = { method: response.request().method(), path: url.pathname, status: response.status() };
        authResponses.push(observed);
        if (url.pathname === "/auth/login") {
          void response.json().then(body => {
            observed.login = {
              access_token_present: typeof body.access_token === "string" && body.access_token.length > 0,
              mfa_required: body.mfa_required === true,
              mfa_setup_required: body.mfa_setup_required === true,
            };
          }).catch(() => {});
        }
      }
    } catch {
      // Only diagnostic response metadata is collected.
    }
  };
  page.on("response", onResponse);
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_PASSWORD);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  try {
    await page.waitForURL(url => !url.pathname.startsWith("/login"));
  } catch (error) {
    const visibleAlertCount = await page.locator('[role="alert"]').count().catch(() => -1);
    const currentPath = new URL(page.url()).pathname;
    throw new Error(`browser login did not leave /login: ${JSON.stringify({ auth_responses: authResponses, current_path: currentPath, visible_alert_count: visibleAlertCount })}`, { cause: error });
  } finally {
    page.off("response", onResponse);
  }
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

try {
  const runs = [];
  for (const batch of batches) {
    const sessions = await Promise.all(batch.map(open));
    try {
      runs.push(...await Promise.all(sessions.map(session => measure(session, "first_load"))));
      runs.push(...await Promise.all(sessions.map(session => measure(session, "steady"))));
    } finally {
      await Promise.all(sessions.map(session => session.context.close()));
    }
  }
  console.log(JSON.stringify({ runs }));
} finally {
  await browser.close();
}
