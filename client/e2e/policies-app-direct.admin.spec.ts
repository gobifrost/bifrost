/**
 * Policies — App Direct REST (Admin)
 *
 * Tripwire for the bundled `tables` SDK round-trip:
 *   - app TSX imports `{ tables }` from "bifrost"
 *   - calls `tables.insert(...)` and `tables.query(...)` against a table
 *     with `admin_bypass + own_row` policies
 *   - admin user is bypassed, so the row inserts and the query returns it
 *   - the call hits REST directly — NO workflow execution is created
 *
 * Replaces the deleted `tables-app-direct.admin.spec.ts` (Task 1 reset).
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-policies-direct-${UNIQUE}`;
const APP_NAME = `E2E Policies Direct ${UNIQUE}`;
const TABLE_NAME = `e2e_policies_direct_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

// The app uses the platform-injected `tables` SDK from "bifrost".
// On click it inserts a row, then queries the table; both calls go to the
// REST API and never spin up a workflow execution.
const INDEX_TSX = `import { tables, useState } from "bifrost";

export default function Home() {
	const [last, setLast] = useState<string>("idle");
	const [rows, setRows] = useState<unknown[]>([]);

	async function onInsert() {
		try {
			const doc: any = await tables.insert("${TABLE_NAME}", { value: "from-app" });
			setLast(\`inserted:\${doc.id}\`);
		} catch (e) {
			setLast(\`error:\${(e as Error).message}\`);
		}
	}

	async function onQuery() {
		try {
			const r = await tables.query("${TABLE_NAME}");
			setRows(r.documents);
			setLast(\`queried:\${r.documents.length}\`);
		} catch (e) {
			setLast(\`error:\${(e as Error).message}\`);
		}
	}

	return (
		<div>
			<button data-testid="insert" onClick={onInsert}>Insert</button>
			<button data-testid="query" onClick={onQuery}>Query</button>
			<div data-testid="last">{last}</div>
			<ul data-testid="rows">
				{rows.map((r: any) => <li key={r.id} data-testid="row">{r.data?.value}</li>)}
			</ul>
		</div>
	);
}
`;

// Matches the CLI's /api/files/write contract.
function writeBody(path: string, content: string) {
	return {
		path,
		content: Buffer.from(content, "utf-8").toString("base64"),
		mode: "cloud",
		location: "workspace",
		binary: true,
	};
}

function trackPageErrors(page: Page): { errors: string[] } {
	const errors: string[] = [];
	page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
	page.on("console", (msg) => {
		if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
	});
	return { errors };
}

test.describe("Policies — App Direct REST", () => {
	let appId: string;
	let tableId: string;

	test.beforeAll(async ({ api }) => {
		// 1. Create the app shell
		const createApp = await api.post("/api/applications", {
			data: {
				name: APP_NAME,
				slug: APP_SLUG,
				access_level: "authenticated",
				role_ids: [],
			},
		});
		expect(createApp.ok(), await createApp.text()).toBe(true);
		appId = (await createApp.json()).id;

		// 2. Create a table with admin_bypass + own_row policies.
		// admin_bypass lets the platform-admin user do everything; own_row
		// would scope non-admins to their own rows. We test the admin path
		// here; the own_row clause is present so the test reflects the
		// realistic multi-policy shape we ship by default.
		const policies = {
			policies: [
				{
					name: "admin_bypass",
					actions: ["read", "create", "update", "delete"],
					when: { user: "is_platform_admin" },
				},
				{
					name: "own_row",
					actions: ["read", "update", "delete"],
					when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
				},
			],
		};
		const createTable = await api.post("/api/tables", {
			data: { name: TABLE_NAME, policies },
		});
		expect(createTable.ok(), await createTable.text()).toBe(true);
		tableId = (await createTable.json()).id;

		// 3. Seed the app source — minimal layout + index using the SDK
		for (const [relPath, source] of [
			[`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
			[`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
		] as const) {
			const r = await api.post("/api/files/write", {
				data: writeBody(relPath, source),
			});
			expect(r.ok(), `write ${relPath}: ${await r.text()}`).toBe(true);
		}
	});

	test.afterAll(async ({ api }) => {
		if (appId) await api.delete(`/api/applications/${appId}`);
		if (tableId) await api.delete(`/api/tables/${tableId}`);
	});

	test("admin inserts + queries via SDK; no workflow execution", async ({
		page,
		api,
	}) => {
		const { errors } = trackPageErrors(page);

		// Snapshot execution count before the test. The SDK path is direct
		// REST → tables router; no execution should be created.
		const before = await api.get("/api/executions?limit=50");
		expect(before.ok(), await before.text()).toBe(true);
		const beforeCount = (await before.json()).executions?.length ?? 0;

		await page.goto(`/apps/${APP_SLUG}/preview`);
		await expect(page.getByTestId("insert")).toBeVisible({ timeout: 30_000 });

		await page.getByTestId("insert").click();
		await expect(page.getByTestId("last")).toContainText("inserted:", {
			timeout: 10_000,
		});

		await page.getByTestId("query").click();
		await expect(page.getByTestId("last")).toContainText("queried:1", {
			timeout: 10_000,
		});
		await expect(page.getByTestId("rows")).toContainText("from-app");

		// Confirm the SDK round-trip did NOT create a workflow execution.
		const after = await api.get("/api/executions?limit=50");
		expect(after.ok(), await after.text()).toBe(true);
		const afterCount = (await after.json()).executions?.length ?? 0;
		expect(afterCount).toBe(beforeCount);

		expect(errors, errors.join("\n")).toEqual([]);
	});
});
