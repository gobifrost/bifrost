/** Browser regression for canonical Apps v2 ES-module identity.
 *
 * Builds a normal Vite/React fixture whose React.lazy chunk imports a shared
 * binding from the entry. If the host queries only the entry URL, the browser
 * evaluates it a second time when the lazy chunk imports the canonical URL.
 */
import { mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";

import react from "@vitejs/plugin-react";
import type { Page } from "@playwright/test";
import { build } from "vite";

import { expect, test } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const SOLUTION_SLUG = `v2-lazy-${UNIQUE}`;
const APP_SLUG = `app-${SOLUTION_SLUG}`;

interface BuiltFixture {
	distFiles: Record<string, string>;
	entry: string;
}

const CRC_TABLE = (() => {
	const table = new Uint32Array(256);
	for (let n = 0; n < 256; n++) {
		let crc = n;
		for (let bit = 0; bit < 8; bit++) {
			crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
		}
		table[n] = crc >>> 0;
	}
	return table;
})();

function crc32(input: Buffer): number {
	let crc = 0xffffffff;
	for (const byte of input)
		crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
	return (crc ^ 0xffffffff) >>> 0;
}

function buildZip(entries: { path: string; content: string }[]): Buffer {
	const localParts: Buffer[] = [];
	const centralParts: Buffer[] = [];
	let offset = 0;
	for (const entry of entries) {
		const name = Buffer.from(entry.path);
		const data = Buffer.from(entry.content);
		const checksum = crc32(data);
		const local = Buffer.alloc(30);
		local.writeUInt32LE(0x04034b50, 0);
		local.writeUInt16LE(20, 4);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(data.length, 18);
		local.writeUInt32LE(data.length, 22);
		local.writeUInt16LE(name.length, 26);
		localParts.push(local, name, data);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(data.length, 20);
		central.writeUInt32LE(data.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt32LE(offset, 42);
		centralParts.push(central, name);
		offset += local.length + name.length + data.length;
	}
	const centralDirectory = Buffer.concat(centralParts);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(entries.length, 8);
	end.writeUInt16LE(entries.length, 10);
	end.writeUInt32LE(centralDirectory.length, 12);
	end.writeUInt32LE(offset, 16);
	return Buffer.concat([...localParts, centralDirectory, end]);
}

function workspaceZip(
	appId: string,
	distFiles: Record<string, string>,
): Buffer {
	return buildZip([
		{
			path: "bifrost.solution.yaml",
			content: JSON.stringify({
				slug: SOLUTION_SLUG,
				name: SOLUTION_SLUG,
				global_repo_access: false,
			}),
		},
		{
			path: ".bifrost/apps.yaml",
			content: JSON.stringify({
				apps: {
					[appId]: {
						id: appId,
						slug: APP_SLUG,
						name: "V2 Lazy Fixture",
						app_model: "standalone_v2",
						dependencies: {},
						access_level: "authenticated",
						path: `apps/${APP_SLUG}`,
						dist_files: distFiles,
					},
				},
			}),
		},
	]);
}

async function buildFixture(): Promise<BuiltFixture> {
	const root = resolve(import.meta.dirname, "fixtures/v2-lazy-app");
	const outDir = mkdtempSync(join(tmpdir(), "bifrost-v2-lazy-"));
	await build({
		root,
		base: "./",
		plugins: [react()],
		build: { outDir, emptyOutDir: true },
		logLevel: "silent",
	});

	const distFiles: Record<string, string> = {};
	const collect = (dir: string) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const path = join(dir, entry.name);
			if (entry.isDirectory()) collect(path);
			else
				distFiles[relative(outDir, path).replaceAll("\\", "/")] =
					readFileSync(path, "utf8");
		}
	};
	collect(outDir);

	const index = distFiles["index.html"];
	const entryMatch = index.match(
		/<script[^>]+src="\.\/(assets\/index-[^"]+\.js)"/,
	);
	if (!entryMatch) throw new Error(`Vite entry not found in ${index}`);
	const entry = entryMatch[1];
	const lazyPath = Object.keys(distFiles).find((path) =>
		/assets\/LazyPage-.*\.js$/.test(path),
	);
	if (!lazyPath) throw new Error("Vite lazy chunk not found");

	// This is the exact dependency shape that exposed the production bug.
	expect(distFiles[lazyPath]).toContain(`from"./${entry.split("/").at(-1)}"`);
	return { distFiles, entry };
}

function trackErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
	page.on("console", (message) => {
		if (message.type() === "error")
			errors.push(`console.error: ${message.text()}`);
	});
	return errors;
}

test.describe("Apps v2 code splitting", () => {
	test("React.lazy evaluates the canonical graph once and remounts explicitly", async ({
		page,
		api,
	}) => {
		test.setTimeout(120_000);
		const built = await buildFixture();
		const appId = crypto.randomUUID();
		const errors = trackErrors(page);
		const entryRequests: string[] = [];
		page.on("request", (request) => {
			if (new URL(request.url()).pathname.endsWith(`/${built.entry}`)) {
				entryRequests.push(request.url());
			}
		});

		const solution = await api.post("/api/solutions", {
			data: {
				slug: SOLUTION_SLUG,
				name: SOLUTION_SLUG,
				scope: "global",
				global_repo_access: false,
			},
		});
		expect(solution.ok(), await solution.text()).toBeTruthy();
		const solutionId = (await solution.json()).id;

		try {
			const deploy = await page
				.context()
				.request.post(`/api/solutions/${solutionId}/deploy`, {
					headers: await api.csrfHeader(),
					multipart: {
						file: {
							name: "solution.zip",
							mimeType: "application/zip",
							buffer: workspaceZip(appId, built.distFiles),
						},
					},
				});
			expect(deploy.status(), await deploy.text()).toBe(202);
			const jobId = (await deploy.json()).deploy_job_id;
			await expect
				.poll(
					async () => {
						const response = await api.get(
							`/api/solutions/deploy-jobs/${jobId}`,
						);
						const body = await response.json();
						if (body.status === "failed")
							throw new Error(body.error);
						return body.status;
					},
					{ timeout: 60_000 },
				)
				.toBe("succeeded");

			await page.goto(`/apps/${APP_SLUG}`);
			await expect(page.getByTestId("lazy-page")).toHaveText(
				"Lazy chunk rendered",
			);
			await expect(page.getByTestId("fixture-basename")).toHaveText(
				`/apps/${APP_SLUG}`,
			);
			expect(await page.evaluate(() => window.__v2LazyFixture)).toEqual({
				entryExecutions: 1,
				mounts: 1,
				unmounts: 0,
			});
			expect(entryRequests).toHaveLength(1);
			expect(entryRequests[0]).not.toContain("?");

			// Leave and re-enter through the host SPA: the module stays evaluated once,
			// while mount() and its returned teardown run for each visit.
			await page.evaluate(() => {
				history.pushState({}, "", "/");
				window.dispatchEvent(new PopStateEvent("popstate"));
			});
			await expect(page).toHaveURL(/\/$/);
			await expect
				.poll(() =>
					page.evaluate(() => window.__v2LazyFixture?.unmounts),
				)
				.toBe(1);

			await page.evaluate((slug) => {
				history.pushState({}, "", `/apps/${slug}`);
				window.dispatchEvent(new PopStateEvent("popstate"));
			}, APP_SLUG);
			await expect(page.getByTestId("lazy-page")).toHaveText(
				"Lazy chunk rendered",
			);
			expect(await page.evaluate(() => window.__v2LazyFixture)).toEqual({
				entryExecutions: 1,
				mounts: 2,
				unmounts: 1,
			});
			expect(entryRequests).toHaveLength(1);
			expect(errors, errors.join("\n")).toEqual([]);
		} finally {
			await api
				.delete(`/api/solutions/${solutionId}`, {
					params: { confirm: SOLUTION_SLUG },
				})
				.catch(() => {});
		}
	});
});
