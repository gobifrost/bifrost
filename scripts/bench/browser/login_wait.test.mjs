import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { chromium } from "playwright";

test("waitForURL's default milestone accepts a React-style history navigation", async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html" });
    response.end(`<!doctype html><button id="sign-in">Sign In</button><script>
      document.querySelector('#sign-in').addEventListener('click', () => history.pushState({}, '', '/after-login'));
    </script>`);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/login`);
    await page.getByRole("button", { name: "Sign In" }).click();
    await page.waitForURL(url => url.pathname === "/after-login", { timeout: 1_000 });
    assert.equal(new URL(page.url()).pathname, "/after-login");
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});

test("Playwright response.json observes a login result without consuming the page response", async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ access_token: "synthetic-token", mfa_required: false }));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/login`);
    const observed = new Promise(resolve => page.on("response", async response => {
      if (new URL(response.url()).pathname === "/auth/login") resolve(await response.json());
    }));
    const pageResult = await page.evaluate(async () => {
      const response = await fetch("/auth/login", { method: "POST" });
      return response.json();
    });
    assert.deepEqual(await observed, { access_token: "synthetic-token", mfa_required: false });
    assert.deepEqual(pageResult, { access_token: "synthetic-token", mfa_required: false });
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
