/**
 * Policies — App Realtime via useTable (Admin)
 *
 * Tripwire for the bundled `useTable` hook end-to-end:
 *   - app TSX imports `{ useTable }` from "bifrost"
 *   - hook fetches an initial snapshot from REST
 *   - hook subscribes to the websocket fanout for live changes
 *   - admin (with admin_bypass + everyone_read) inserts a row via REST,
 *     and the new row appears in the rendered DOM within a few seconds
 *
 * Replaces the deleted `tables-app-subscription.admin.spec.ts` (Task 1 reset),
 * which had used the now-removed `useTableSubscription` low-level hook.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-policies-realtime-${UNIQUE}`;
const APP_NAME = `E2E Policies Realtime ${UNIQUE}`;
const TABLE_NAME = `e2e_policies_realtime_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

// The app uses the platform-injected `useTable` hook from "bifrost".
// We pass the table id via search params so the seed source stays
// parameter-free. useTable returns { rows, loading, error } and applies
// websocket events to local state. The websocket subscribe protocol uses
// `table:{id}` channels, so the hook is called with the id (not the name).
//
// Row shape note: snapshot rows from `tables.query` come back as
// `DocumentPublic` (nested `data`), while websocket-delivered rows are
// pre-flattened by the API (`_row_from_doc` flattens jsonb keys to the top
// level). The renderer falls back across both shapes so this spec exercises
// the live update path regardless of which side delivered the row.
const INDEX_TSX = `import { useTable, useSearchParams } from "bifrost";

export default function Home() {
	const [params] = useSearchParams();
	const tableId = params.get("table") ?? "";
	const { rows, loading, error } = useTable(tableId);

	return (
		<div>
			<div data-testid="status">
				{loading ? "loading" : error ? \`error:\${error.message}\` : "ready"}
			</div>
			<div data-testid="count">{rows.length}</div>
			<ul data-testid="rows">
				{rows.map((r: any) => (
					<li key={r.id} data-testid="row">{r.data?.value ?? r.value}</li>
				))}
			</ul>
		</div>
	);
}
`;

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

test.describe("Policies — App Realtime via useTable", () => {
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

		// 2. Create a table with admin_bypass + everyone_read.
		// `when: null` is the "always-allow" / everyone policy — every
		// authenticated user satisfies the read rule, so the websocket
		// subscribe is accepted and the snapshot includes all rows.
		const policies = {
			policies: [
				{
					name: "admin_bypass",
					actions: ["read", "create", "update", "delete"],
					when: { user: "is_platform_admin" },
				},
				{
					name: "everyone_read",
					actions: ["read"],
					when: null,
				},
			],
		};
		const createTable = await api.post("/api/tables", {
			data: { name: TABLE_NAME, policies },
		});
		expect(createTable.ok(), await createTable.text()).toBe(true);
		tableId = (await createTable.json()).id;

		// 3. Seed the app source
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

	test("useTable shows initial snapshot and reflects a REST insert via websocket", async ({
		page,
		api,
	}) => {
		const { errors } = trackPageErrors(page);

		await page.goto(
			`/apps/${APP_SLUG}/preview?table=${encodeURIComponent(tableId)}`,
		);

		// Initial snapshot should resolve quickly to "ready" with 0 rows.
		await expect(page.getByTestId("status")).toHaveText("ready", {
			timeout: 30_000,
		});
		await expect(page.getByTestId("count")).toHaveText("0");

		// Give the websocket a moment to finish subscribing before we
		// trigger the REST insert. useTable subscribes inside an effect
		// after the snapshot resolves; without this delay the test races
		// against the SUBSCRIBE handshake.
		await page.waitForTimeout(750);

		// Insert a row via REST as the admin. The websocket fanout should
		// deliver the change to the page, and useTable applies it to local
		// state — the row must appear in the DOM.
		const insert = await api.post(
			`/api/tables/${tableId}/documents`,
			{ data: { data: { value: "from-rest" } } },
		);
		expect(insert.ok(), await insert.text()).toBe(true);

		await expect(page.getByTestId("rows")).toContainText("from-rest", {
			timeout: 5000,
		});
		await expect(page.getByTestId("count")).toHaveText("1");

		expect(errors, errors.join("\n")).toEqual([]);
	});
});
