import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { expect, test } from "@playwright/test";
import { build } from "vite";

// No platform jobs: build the real SDK component with production React. React
// 18 is a supported SDK peer used by independent apps; the host uses React 19.
for (const renderer of ["React 18", "current React"]) {
	test(
		`restores desktop SDK controls after phone resizing without a header click (${renderer})`,
		{ tag: "@smoke" },
		async ({ page }) => {
			const outDir = await mkdtemp(join(tmpdir(), "bifrost-sdk-header-"));
			try {
				await build({
					configFile: false,
					root: resolve(import.meta.dirname, "fixtures/sdk-header"),
					base: "./",
					plugins: [react()],
					resolve: {
						alias:
							renderer === "React 18"
								? {
										react: "/opt/sdk-header-react18/node_modules/react",
										"react-dom":
											"/opt/sdk-header-react18/node_modules/react-dom",
									}
								: {},
					},
					build: { outDir, emptyOutDir: true },
					logLevel: "silent",
				});
				await page.route(
					"**/__sdk-header-fixture/**",
					async (route) => {
						const pathname = new URL(route.request().url())
							.pathname;
						const name =
							pathname.split("/__sdk-header-fixture/")[1] ||
							"index.html";
						if (name.includes(".."))
							throw new Error("Unexpected fixture asset path");
						await route.fulfill({
							body: await readFile(join(outDir, name)),
							contentType: name.endsWith(".js")
								? "application/javascript"
								: "text/html",
						});
					},
				);
				await page.setViewportSize({ width: 1920, height: 1080 });
				await page.goto("/__sdk-header-fixture/");
				await expect(
					page.getByText("SDK Viewer", { exact: true }),
				).toBeVisible();
				await page.setViewportSize({ width: 390, height: 844 });
				await expect(
					page.getByRole("button", {
						name: "Open menu",
						exact: true,
					}),
				).toBeVisible();
				await page.setViewportSize({ width: 320, height: 568 });
				await page.setViewportSize({ width: 1920, height: 1080 });
				await expect(
					page.getByRole("button", {
						name: "Organization",
						exact: true,
					}),
				).toBeVisible();
				await expect(
					page.getByRole("button", {
						name: "Account menu",
						exact: true,
					}),
				).toBeVisible();
				await expect(
					page.getByRole("button", {
						name: "Open menu",
						exact: true,
					}),
				).toHaveCount(0);
			} finally {
				await page.unroute("**/__sdk-header-fixture/**");
				await rm(outDir, { recursive: true, force: true });
			}
		},
	);
}
