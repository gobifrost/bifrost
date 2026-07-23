/**
 * Golden deployed-app runtime-scope contract (Chromium, real deploy).
 *
 * Deploys a Solution workspace zip (workflow + table + file location) with a
 * standalone_v2 app whose entry module uses ONLY the transport contract —
 * Authorization + X-Bifrost-App headers, a portable path::fn workflow ref,
 * no UUIDs, no body app_id — and asserts workflow, table, and file access
 * all resolve the install's own resources in a real browser.
 *
 * This is the regression net for the 2026-06/07 runtime-scope split: each
 * data plane (workflows, tables, files) previously derived install scope its
 * own way, so deployed apps worked for UUID workflow refs but 404'd portable
 * refs. The contract is: auth derives ctx.solution_id from the transport;
 * every resolver reads the context. See
 * api/src/repositories/README.md ("How the install id is DERIVED").
 */

import { test, expect } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
const SLUG = `runtime-contract-${UNIQUE}`;
const APP_SLUG = `app-${SLUG}`;
const TABLE_NAME = `runtime_items_${UNIQUE}`;

const WORKFLOW_PY = [
	"from bifrost import tables, workflow",
	"",
	"@workflow",
	"async def main():",
	`    await tables.upsert(${JSON.stringify(TABLE_NAME)}, id='probe-row', data={'k': 'v'})`,
	"    return {'marker': 'golden'}",
	"",
].join("\n");

// The app entry: a plain ES module (no build step). The shell sets
// window.__BIFROST_APP__ BEFORE importing it (StandaloneV2App contract).
// It exercises the three data planes with header-only scoping and renders
// one data-testid marker per plane.
const ENTRY_JS = `
const boot = window.__BIFROST_APP__;
const el = boot.mountEl;
const h = {
  Authorization: "Bearer " + boot.token,
  "X-Bifrost-App": boot.appId,
  "Content-Type": "application/json",
};
const call = (path, init) =>
  fetch(boot.baseUrl + path, { credentials: "omit", ...init, headers: h });
const mark = (id, txt) => {
  const d = document.createElement("div");
  d.dataset.testid = id;
  d.textContent = txt;
  el.appendChild(d);
};
(async () => {
  try {
    const wf = await call("/api/workflows/execute", {
      method: "POST",
      body: JSON.stringify({ workflow_id: "workflows/runtime.py::main", sync: true }),
    }).then((r) => r.json());
    mark("workflow-result", (wf.result && wf.result.marker) || "FAIL:" + JSON.stringify(wf).slice(0, 300));

    const tq = await call("/api/tables/${TABLE_NAME}/documents/query", {
      method: "POST",
      body: JSON.stringify({ limit: 10 }),
    }).then((r) => r.json());
    const docs = Array.isArray(tq) ? tq : tq.documents;
    mark("table-result", Array.isArray(docs) ? "rows:" + docs.length : "FAIL:" + JSON.stringify(tq).slice(0, 300));

    const wr = await call("/api/files/write", {
      method: "POST",
      body: JSON.stringify({ path: "probe.txt", content: "ok", location: "docs", mode: "cloud", scope: null, binary: false }),
    });
    if (!wr.ok) {
      mark("file-result", "FAIL:write:" + wr.status);
      return;
    }
    const rd = await call("/api/files/read", {
      method: "POST",
      body: JSON.stringify({ path: "probe.txt", location: "docs", mode: "cloud", scope: null, binary: false }),
    }).then((r) => r.json());
    mark("file-result", rd.content === "ok" ? "ok" : "FAIL:" + JSON.stringify(rd).slice(0, 300));
  } catch (e) {
    mark("fatal", String(e));
  }
})();
`;

const INDEX_HTML = `<!doctype html><html><body><div id="root"></div><script type="module" src="/dist/main.js"></script></body></html>`;

// ---------------------------------------------------------------------------
// Minimal store-only zip writer (same approach as
// solution-files-link.admin.spec.ts — no zip dependency in the e2e runner).
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
	const table = new Uint32Array(256);
	for (let n = 0; n < 256; n++) {
		let c = n;
		for (let k = 0; k < 8; k++) {
			c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
		}
		table[n] = c >>> 0;
	}
	return table;
})();

function crc32(input: Buffer): number {
	let crc = 0xffffffff;
	for (const byte of input) {
		crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
	}
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
		local.writeUInt16LE(0, 6);
		local.writeUInt16LE(0, 8);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(data.length, 18);
		local.writeUInt32LE(data.length, 22);
		local.writeUInt16LE(name.length, 26);
		local.writeUInt16LE(0, 28);
		localParts.push(local, name, data);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt16LE(0, 8);
		central.writeUInt16LE(0, 10);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(data.length, 20);
		central.writeUInt32LE(data.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt16LE(0, 30);
		central.writeUInt16LE(0, 32);
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

// YAML members written as JSON — YAML 1.2 parses JSON, and it spares the
// runner a yaml dependency.
function workspaceZip(): Buffer {
	const workflowId = crypto.randomUUID();
	const tableId = crypto.randomUUID();
	const appId = crypto.randomUUID();
	return buildZip([
		{
			path: "bifrost.solution.yaml",
			content: JSON.stringify({
				slug: SLUG,
				name: SLUG.toUpperCase(),
				global_repo_access: false,
			}),
		},
		{ path: "workflows/runtime.py", content: WORKFLOW_PY },
		{
			path: ".bifrost/workflows.yaml",
			content: JSON.stringify({
				workflows: {
					[workflowId]: {
						id: workflowId,
						name: `runtime_${UNIQUE}`,
						function_name: "main",
						path: "workflows/runtime.py",
						type: "workflow",
					},
				},
			}),
		},
		{
			path: ".bifrost/tables.yaml",
			content: JSON.stringify({
				tables: {
					[tableId]: {
						id: tableId,
						name: TABLE_NAME,
						schema: { columns: [{ name: "k" }] },
						policies: null,
					},
				},
			}),
		},
		{
			path: ".bifrost/files.yaml",
			content: JSON.stringify({ locations: ["docs"] }),
		},
		{
			path: ".bifrost/apps.yaml",
			content: JSON.stringify({
				apps: {
					[appId]: {
						id: appId,
						slug: APP_SLUG,
						name: "Runtime Contract",
						app_model: "standalone_v2",
						dependencies: {},
						access_level: "authenticated",
						path: `apps/${APP_SLUG}`,
						// Prebuilt fast-path: no src_files, dist carried inline.
						dist_files: {
							"index.html": INDEX_HTML,
							"main.js": ENTRY_JS,
						},
					},
				},
			}),
		},
	]);
}

test.describe("deployed solution runtime contract", () => {
	let createdSolutionId: string | null = null;

	test.afterEach(async ({ api }) => {
		if (!createdSolutionId) return;
		const cleanup = await api.delete(
			`/api/solutions/${createdSolutionId}`,
			{
				params: { confirm: SLUG },
			},
		);
		expect(cleanup.ok(), await cleanup.text()).toBeTruthy();
		createdSolutionId = null;
	});

	test("workflow/table/file resolve via the header contract in the browser", async ({
		page,
		api,
	}) => {
		test.setTimeout(120_000);

		// No UUID workaround: the fixture app module must carry no UUID.
		expect(ENTRY_JS).not.toMatch(
			/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
		);

		// --- create the install, then deploy the workspace zip ---
		const sol = await api.post("/api/solutions", {
			data: {
				slug: SLUG,
				name: SLUG,
				organization_id: null,
				global_repo_access: false,
			},
		});
		expect(sol.ok(), await sol.text()).toBeTruthy();
		const sid = (await sol.json()).id;
		createdSolutionId = sid;

		const dep = await page
			.context()
			.request.post(`/api/solutions/${sid}/deploy`, {
				headers: await api.csrfHeader(),
				multipart: {
					file: {
						name: "solution.zip",
						mimeType: "application/zip",
						buffer: workspaceZip(),
					},
				},
			});
		expect(dep.status(), await dep.text()).toBe(202);
		const jobId = (await dep.json()).deploy_job_id;
		await expect
			.poll(
				async () => {
					const st = await api.get(
						`/api/solutions/deploy-jobs/${jobId}`,
					);
					const body = await st.json();
					if (body.status === "failed") {
						throw new Error(`deploy failed: ${body.error}`);
					}
					return body.status;
				},
				{ timeout: 60_000 },
			)
			.toBe("succeeded");

		// --- drive the deployed app in the browser ---
		await page.goto(`/apps/${APP_SLUG}`);
		await expect(page.getByTestId("workflow-result")).toHaveText("golden", {
			timeout: 30_000,
		});
		await expect(page.getByTestId("table-result")).toHaveText(/rows:[1-9]/);
		await expect(page.getByTestId("file-result")).toHaveText("ok");
	});
});
