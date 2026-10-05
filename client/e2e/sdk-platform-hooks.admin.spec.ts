import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { build } from "vite";

import type { components } from "../src/lib/v1";
import { createBrandPalette } from "../src/lib/app-sdk/brand-palette";
import { test, expect } from "./fixtures/api-fixture";

function cssColor(hex: string): string {
	return `rgb(${[1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16)).join(", ")})`;
}

// No platform jobs: download the actual SDK tarball and run it in a standalone
// React fixture against live endpoints, with the caller's normal session.
test(
	"loads branding and organizations from the packaged SDK",
	{ tag: "@smoke" },
	async ({ api, page }) => {
		const workspace = await mkdtemp(join(tmpdir(), "bifrost-sdk-hooks-"));
		try {
			const sdk = await api.get("/api/sdk/download");
			expect(sdk.ok()).toBe(true);
			const archive = join(workspace, "sdk.tgz");
			await writeFile(archive, await sdk.body());
			execFileSync("tar", ["-xzf", archive, "-C", workspace]);
			const outDir = join(workspace, "dist");
			await build({
				configFile: false,
				root: resolve(
					import.meta.dirname,
					"fixtures/sdk-platform-hooks",
				),
				base: "./",
				plugins: [react()],
				resolve: {
					alias: {
						bifrost: join(workspace, "package/dist/index.mjs"),
					},
					dedupe: ["react", "react-dom", "lucide-react"],
				},
				build: { outDir, emptyOutDir: true },
				logLevel: "silent",
			});
			await page.route("**/__sdk-hooks-fixture/**", async (route) => {
				const pathname = new URL(route.request().url()).pathname;
				const name =
					pathname.split("/__sdk-hooks-fixture/")[1] || "index.html";
				if (name.includes(".."))
					throw new Error("Unexpected fixture asset path");
				await route.fulfill({
					body: await readFile(join(outDir, name)),
					contentType: name.endsWith(".js")
						? "application/javascript"
						: "text/html",
				});
			});

			const brandingResponse = await api.get("/api/branding");
			expect(brandingResponse.ok()).toBe(true);
			const branding: components["schemas"]["BrandingSettings"] =
				await brandingResponse.json();
			const orgResponse = await api.get("/api/organizations");
			expect(orgResponse.ok()).toBe(true);
			const orgs: components["schemas"]["OrganizationPublic"][] =
				await orgResponse.json();
			expect(orgs.length).toBeGreaterThan(0);
			const palette = createBrandPalette(branding.primary_color);
			await page.goto("/__sdk-hooks-fixture/");
			await expect(
				page.getByRole("heading", { name: "SDK platform hooks" }),
			).toBeVisible();
			await expect(
				page.getByLabel("Organization").getByRole("option"),
			).toHaveText(orgs.map((org) => org.name));
			await expect(page.getByLabel("Palette color")).toHaveText(
				palette.light.primary,
			);
			await expect(
				page.getByRole("button", { name: "Toggle theme" }),
			).toHaveCSS("background-color", cssColor(palette.light.primary));
			await page.getByRole("button", { name: "Toggle theme" }).click();
			await expect(page.getByLabel("Palette color")).toHaveText(
				palette.dark.primary,
			);
			await expect(
				page.getByRole("button", { name: "Toggle theme" }),
			).toHaveCSS("background-color", cssColor(palette.dark.primary));
		} finally {
			await page.unroute("**/__sdk-hooks-fixture/**");
			await rm(workspace, { recursive: true, force: true });
		}
	},
);
