/** One browser path: the current wire-contract-3 legacy full SDK keeps working,
 * then a hosted App adopts a compatible SDK update on reload. This does not
 * claim compatibility with arbitrary historical SDK binaries. No worker/build
 * jobs are added: both fixtures are compiled once by Vite and their immutable
 * assets are supplied by the test. The hosted factory is real API/shared storage.
 */
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { build } from "vite";
import { expect, test } from "./fixtures/api-fixture";

test("a legacy App keeps working before a hosted App picks up a compatible SDK update", async ({ page, api }) => {
  const root = resolve(import.meta.dirname, "fixtures/hosted-sdk-app");
  const legacyOutDir = mkdtempSync(join(tmpdir(), "bifrost-legacy-sdk-browser-"));
  const hostedOutDir = mkdtempSync(join(tmpdir(), "bifrost-hosted-sdk-browser-"));
  let legacyAppId: string | undefined;
  let hostedAppId: string | undefined;
  try {
    const buildFixture = async (outDir: string, sdkEntry: string) => {
      await build({
        root, base: "./", configFile: false, plugins: [react()], logLevel: "silent",
        resolve: { alias: { bifrost: sdkEntry } },
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
      return { assets, entry: entry! };
    };

    const legacy = await buildFixture(
      legacyOutDir,
      resolve(import.meta.dirname, "../src/lib/app-sdk/index.v2.ts"),
    );
    const hosted = await buildFixture(
      hostedOutDir,
      resolve(import.meta.dirname, "../src/lib/app-sdk/hosted-binding.tsx"),
    );
    const entryBefore = hosted.assets.get(hosted.entry)!;
    // The hosted App contains the binding, not a frozen WebSocket implementation.
    expect(entryBefore).not.toContain("table_invalidated");

    const versionResponse = await api.get("/api/version");
    expect(versionResponse.ok(), await versionResponse.text()).toBeTruthy();
    expect((await versionResponse.json()).sdk_contract_version).toBe(3);

    const descriptorResponse = await api.get("/api/app-sdk/v1/manifest");
    expect(descriptorResponse.ok(), await descriptorResponse.text()).toBeTruthy();
    const first = await descriptorResponse.json();
    const scriptResponse = await api.get(first.asset_url);
    expect(scriptResponse.ok(), await scriptResponse.text()).toBeTruthy();
    const original = await scriptResponse.text();
    expect(createHash("sha256").update(original).digest("hex")).toBe(first.fingerprint);

    const createApp = async (name: string, prefix: string) => {
      const slug = `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
      const created = await api.post("/api/applications", {
        data: { name, slug, app_model: "standalone_v2" },
      });
      expect(created.ok(), await created.text()).toBeTruthy();
      return { id: (await created.json()).id as string, slug };
    };
    const serveAssets = (appId: string, assets: Map<string, string>) => {
      return page.route(`**/api/applications/${appId}/dist/**`, (route) => {
        const path = new URL(route.request().url()).pathname.split("/dist/")[1];
        const body = assets.get(path);
        return route.fulfill({
          status: body === undefined ? 404 : 200,
          contentType: path.endsWith(".css") ? "text/css" : "application/javascript",
          body: body ?? "missing fixture asset",
        });
      });
    };

    const legacyApp = await createApp("Legacy SDK Fixture", "legacy-sdk");
    legacyAppId = legacyApp.id;
    const legacyManifest = {
      entry: legacy.entry, css: null, base_url: `/api/applications/${legacyApp.id}/dist`, mode: "preview",
      dependencies: {}, migrated: false, organization_id: null,
      app_model: "standalone_v2", runtime_contract: "mount-v1", sdk_binding_api_major: null,
    };
    await page.route(`**/api/applications/${legacyApp.id}/bundle-manifest*`, route => route.fulfill({ json: legacyManifest }));
    await serveAssets(legacyApp.id, legacy.assets);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    const legacyHostedRequests: string[] = [];
    page.on("request", request => {
      if (new URL(request.url()).pathname.startsWith("/api/app-sdk/")) legacyHostedRequests.push(request.url());
    });
    await page.goto(`/apps/${legacyApp.slug}/preview`);
    await expect(page.getByRole("heading", { name: "Hosted SDK Content", exact: true })).toBeVisible();
    await expect(page.getByLabel("SDK Theme")).toHaveText("light");
    await expect(page.getByLabel("SDK Connection")).toHaveText("Connected");
    expect(legacyHostedRequests).toEqual([]);

    const hostedApp = await createApp("Hosted SDK Fixture", "hosted-sdk");
    hostedAppId = hostedApp.id;
    const beforeResponse = await api.get(`/api/applications/${hostedApp.slug}`);
    const before = await beforeResponse.json();
    const hostedManifest = {
      entry: hosted.entry, css: null, base_url: `/api/applications/${hostedApp.id}/dist`, mode: "preview",
      dependencies: {}, migrated: false, organization_id: null,
      app_model: "standalone_v2", runtime_contract: "mount-v1", sdk_binding_api_major: 1,
    };
    await page.route(`**/api/applications/${hostedApp.id}/bundle-manifest*`, route => route.fulfill({ json: hostedManifest }));
    await serveAssets(hostedApp.id, hosted.assets);
    await page.goto(`/apps/${hostedApp.slug}/preview`);
    await expect(page.getByRole("heading", { name: "Hosted SDK Content", exact: true })).toBeVisible();
    await expect(page.getByLabel("SDK Theme")).toHaveText("light");
    await expect(page.getByLabel("SDK Connection")).toHaveText("Connected");

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
    await expect(page.getByLabel("SDK Connection")).toHaveText("Connected");
    const after = await (await api.get(`/api/applications/${hostedApp.slug}`)).json();
    expect(after.sdk_built_at).toBe(before.sdk_built_at);
    expect(after.sdk_fingerprint).toBe(before.sdk_fingerprint);
    expect(hosted.assets.get(hosted.entry)).toBe(entryBefore);
    expect(errors).toEqual([]);
  } finally {
    if (hostedAppId) {
      const deleted = await api.delete(`/api/applications/${hostedAppId}`);
      expect(deleted.ok(), await deleted.text()).toBeTruthy();
    }
    if (legacyAppId) {
      const deleted = await api.delete(`/api/applications/${legacyAppId}`);
      expect(deleted.ok(), await deleted.text()).toBeTruthy();
    }
    rmSync(legacyOutDir, { recursive: true, force: true });
    rmSync(hostedOutDir, { recursive: true, force: true });
  }
});
