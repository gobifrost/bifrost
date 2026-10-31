/** One browser path: an unchanged prebuilt App adopts a new hosted SDK on reload.
 * No worker/build jobs are added: the fixture is compiled once by Vite, its
 * immutable asset responses are supplied by the test, and the first SDK factory
 * is served by the real API/shared storage. A second integrity-valid artifact
 * changes one existing SDK hook's behavior to model a compatible SDK update.
 */
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { build } from "vite";
import { expect, test } from "./fixtures/api-fixture";

test("an App picks up a compatible hosted SDK update without rebuilding", async ({ page, api }) => {
  const root = resolve(import.meta.dirname, "fixtures/hosted-sdk-app");
  const outDir = mkdtempSync(join(tmpdir(), "bifrost-hosted-sdk-browser-"));
  let appId: string | undefined;
  try {
    await build({
      root, base: "./", configFile: false, plugins: [react()], logLevel: "silent",
      resolve: { alias: { bifrost: resolve(import.meta.dirname, "../src/lib/app-sdk/hosted-binding.tsx") } },
      build: { outDir, emptyOutDir: true, target: "es2020" },
    });
    const assets = new Map<string, string>();
    const collect = (directory: string) => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) collect(path);
        else assets.set(relative(outDir, path).replaceAll("\\", "/"), readFileSync(path, "utf8"));
      }
    };
    collect(outDir);
    const entry = assets.get("index.html")?.match(/<script[^>]+src="\.\/(assets\/[^"]+\.js)"/)?.[1];
    expect(entry).toBeTruthy();
    const entryBefore = assets.get(entry!)!;
    // The App contains the binding, not a frozen WebSocket implementation.
    expect(entryBefore).not.toContain("table_invalidated");

    const descriptorResponse = await api.get("/api/app-sdk/v1/manifest");
    expect(descriptorResponse.ok(), await descriptorResponse.text()).toBeTruthy();
    const first = await descriptorResponse.json();
    const scriptResponse = await api.get(first.asset_url);
    expect(scriptResponse.ok(), await scriptResponse.text()).toBeTruthy();
    const original = await scriptResponse.text();
    expect(createHash("sha256").update(original).digest("hex")).toBe(first.fingerprint);

    const slug = `hosted-sdk-${crypto.randomUUID().slice(0, 8)}`;
    const created = await api.post("/api/applications", { data: { name: "Hosted SDK Fixture", slug, app_model: "standalone_v2" } });
    expect(created.ok(), await created.text()).toBeTruthy();
    appId = (await created.json()).id;
    const beforeResponse = await api.get(`/api/applications/${slug}`);
    const before = await beforeResponse.json();
    const manifest = {
      entry, css: null, base_url: `/api/applications/${appId}/dist`, mode: "preview",
      dependencies: {}, migrated: false, organization_id: null,
      app_model: "standalone_v2", runtime_contract: "mount-v1", sdk_binding_api_major: 1,
    };
    await page.route(`**/api/applications/${appId}/bundle-manifest*`, route => route.fulfill({ json: manifest }));
    await page.route(`**/api/applications/${appId}/dist/**`, route => {
      const path = new URL(route.request().url()).pathname.split("/dist/")[1];
      const body = assets.get(path);
      return route.fulfill({ status: body === undefined ? 404 : 200, contentType: "application/javascript", body: body ?? "missing fixture asset" });
    });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`/apps/${slug}/preview`);
    await expect(page.getByRole("heading", { name: "Hosted SDK Content", exact: true })).toBeVisible();
    await expect(page.getByLabel("SDK Theme")).toHaveText("light");

    // A new implementation preserves the public hook signature while changing
    // behavior. No App compilation, deployment, or new entry URL occurs here.
    const factoryId = createHash("sha256").update(`fixture:${first.factory_id}`).digest("hex");
    const secondScript = original + `\n(() => { const r = globalThis.__BIFROST_APP_SDK_FACTORIES_V1__; const original = r[${JSON.stringify(first.factory_id)}]; r[${JSON.stringify(factoryId)}] = { createSdk(peers) { const sdk = original.createSdk(peers); return { ...sdk, useBifrostContext() { return { ...sdk.useBifrostContext(), theme: "dark" }; } }; } }; })();\n`;
    const digest = createHash("sha256").update(secondScript).digest();
    const fingerprint = digest.toString("hex");
    const second = { ...first, factory_id: factoryId, fingerprint, integrity: `sha256-${digest.toString("base64")}`, asset_url: `/api/app-sdk/assets/${fingerprint}.js` };
    await page.route("**/api/app-sdk/v1/manifest", route => route.fulfill({ json: second, headers: { "Cache-Control": "no-store" } }));
    await page.route(`**${second.asset_url}`, route => route.fulfill({ body: secondScript, contentType: "application/javascript", headers: { "Cache-Control": "public, max-age=31536000, immutable" } }));
    await page.reload();
    await expect(page.getByLabel("SDK Theme")).toHaveText("dark");
    const after = await (await api.get(`/api/applications/${slug}`)).json();
    expect(after.sdk_built_at).toBe(before.sdk_built_at);
    expect(after.sdk_fingerprint).toBe(before.sdk_fingerprint);
    expect(assets.get(entry!)).toBe(entryBefore);
    expect(errors).toEqual([]);
  } finally {
    if (appId) {
      const deleted = await api.delete(`/api/applications/${appId}`);
      expect(deleted.ok(), await deleted.text()).toBeTruthy();
    }
    rmSync(outDir, { recursive: true, force: true });
  }
});
