import { chromium } from "playwright";
import { browserObserverScript } from "./observer.mjs";

const config = JSON.parse(process.argv[2]);
const batches = config.batches;
if (!Array.isArray(batches) || !batches.length || batches.some(batch => !Array.isArray(batch) || batch.length !== 3)) {
  throw new Error("browser benchmark needs nonempty three-page observation batches");
}
const AUTH_PATHS = new Set(["/auth/status", "/auth/login", "/auth/me", "/auth/refresh", "/api/auth/refresh"]);
const clientOrigin = new URL(config.client_url).origin;
const browser = await chromium.launch({ headless: true });

function safePath(url) {
  try { return new URL(url, clientOrigin).pathname; } catch { return "[invalid-url]"; }
}

function safeError(value) {
  let text = String(value ?? "");
  for (const secret of [process.env.BIFROST_DEFAULT_USER_EMAIL, process.env.BIFROST_DEFAULT_USER_PASSWORD]) {
    if (secret) text = text.replaceAll(secret, "[redacted]");
  }
  return text
    .replace(/https?:\/\/[^\s'\"]+/g, url => safePath(url))
    .replace(/\?[^\s]*/g, "?[redacted]");
}

async function login(ordinal) {
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
  const diagnostic = { ordinal, stage: "new", current_path: null, auth_statuses: [], failed_assets: [] };
  const onResponse = response => {
    try {
      const url = new URL(response.url());
      if (url.origin === clientOrigin && AUTH_PATHS.has(url.pathname)) {
        diagnostic.auth_statuses.push({ method: response.request().method(), path: url.pathname, status: response.status() });
      }
    } catch {}
  };
  const onFailed = request => diagnostic.failed_assets.push({ path: safePath(request.url()), error: safeError(request.failure()?.errorText) });
  page.on("response", onResponse);
  page.on("requestfailed", onFailed);
  try {
    diagnostic.stage = "goto_login";
    await page.goto("/login");
    diagnostic.stage = "fill_email";
    await page.getByLabel("Email", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_EMAIL);
    diagnostic.stage = "fill_password";
    await page.getByLabel("Password", { exact: true }).fill(process.env.BIFROST_DEFAULT_USER_PASSWORD);
    diagnostic.stage = "click_sign_in";
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    diagnostic.stage = "wait_for_login_exit";
    await page.waitForURL(url => !url.pathname.startsWith("/login"));
    diagnostic.stage = "authenticated";
  } catch {
    diagnostic.current_path = safePath(page.url());
    page.off("response", onResponse);
    page.off("requestfailed", onFailed);
    await context.close();
    throw new Error(`browser login did not complete: ${JSON.stringify(diagnostic)}`);
  }
  diagnostic.current_path = safePath(page.url());
  page.off("response", onResponse);
  page.off("requestfailed", onFailed);
  return { context, page, ordinal, login: diagnostic };
}

async function visit(session, { variant, ordinal }) {
  await session.page.goto(`/apps/${config.app_slug}?variant=${variant}`);
  return { ...session, variant, ordinal };
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
  // Authenticate the three concurrent pages once. Each variant navigation is
  // complete before timing starts, so cache fills and React page resets stay
  // outside the action-to-render interval.
  const sessions = await Promise.all(batches[0].map(({ ordinal }) => login(ordinal)));
  try {
    const runs = [];
    for (const batch of batches) {
      const pages = await Promise.all(batch.map((page, index) => visit(sessions[index], page)));
      runs.push(...await Promise.all(pages.map(page => measure(page, "first_load"))));
      runs.push(...await Promise.all(pages.map(page => measure(page, "steady"))));
    }
    console.log(JSON.stringify({ runs, logins: sessions.map(session => session.login), context_count: sessions.length }));
  } finally {
    await Promise.all(sessions.map(session => session.context.close()));
  }
} finally {
  await browser.close();
}
