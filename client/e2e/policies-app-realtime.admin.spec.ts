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

import { test, expect, csrfHeader } from "./fixtures/api-fixture";
import type { BrowserContext, Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import {
	getAuthStatePath,
	getCredentialsPath,
	type UserCredentials,
} from "./fixtures/users";

// ESM equivalent of __dirname for resolving auth fixture files saved by global setup.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-policies-realtime-${UNIQUE}`;
const APP_NAME = `E2E Policies Realtime ${UNIQUE}`;
const TABLE_NAME = `e2e_policies_realtime_${UNIQUE}`;
const VIS_TABLE_NAME = `e2e_policies_visibility_${UNIQUE}`;
const VIS_APP_SLUG = `e2e-policies-visibility-${UNIQUE}`;
const VIS_APP_NAME = `E2E Policies Visibility ${UNIQUE}`;

function loadCredentials(): Record<string, UserCredentials> {
	const credPath = path.resolve(__dirname, getCredentialsPath());
	if (!fs.existsSync(credPath)) {
		throw new Error(
			`Credentials file not found at ${credPath}. Run setup first.`,
		);
	}
	return JSON.parse(fs.readFileSync(credPath, "utf-8"));
}

async function postAs(
	ctx: BrowserContext,
	url: string,
	body: unknown,
): Promise<{ ok: boolean; status: number; text: string; json: unknown }> {
	const headers = await csrfHeader(ctx);
	const res = await ctx.request.fetch(url, {
		method: "POST",
		headers,
		data: body,
	});
	const text = await res.text();
	let json: unknown = null;
	try {
		json = JSON.parse(text);
	} catch {
		// non-JSON response (e.g. error page)
	}
	return { ok: res.ok(), status: res.status(), text, json };
}

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

// The app uses the platform-injected `useTable` hook from "bifrost".
// We pass the table id via search params so the seed source stays
// parameter-free. useTable returns { rows, loading, error } and applies
// websocket events to local state.
//
// Row shape: useTable normalizes snapshot rows to the flat shape websocket
// events deliver (jsonb fields spread at the top level alongside id /
// created_by / etc), so consumers see `r.value` regardless of which side —
// snapshot or live update — delivered the row.
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
					<li key={r.id} data-testid="row">{r.value as string}</li>
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

/**
 * Policies — Visibility-gain via reassignment (Admin)
 *
 * Tripwire for the websocket "became-visible" path: a row that was
 * invisible to a subscriber becomes visible after a PATCH that updates
 * a denormalized user_id field, and useTable applies it as an INSERT.
 *
 * Setup: a table with `admin_bypass + everyone_create + own_user_id (read)`
 * where `own_user_id` is `eq[row.user_id, user.user_id]`.
 *
 * Flow:
 *   1) Bob (org2_user) inserts a row with `data.user_id = bob.userId`.
 *      Alice cannot read it (policy gates on row.user_id == user.user_id).
 *   2) Alice (org1_user) opens the published app and `useTable` subscribes.
 *      Initial snapshot is empty (count=0).
 *   3) Admin PATCHes the row's `data.user_id` to alice.userId.
 *      The websocket fanout decides "became_visible" for Alice's
 *      subscription and emits an INSERT, which useTable applies →
 *      Alice's UI re-renders with count=1 and the row's value visible.
 *
 * Bob is a non-admin so admin_bypass cannot short-circuit visibility on
 * the read side. The policy includes admin_bypass so the admin's PATCH
 * succeeds without satisfying `own_user_id`.
 */
test.describe("Policies — Visibility-gain via reassignment", () => {
	let appId: string;
	let tableId: string;
	let docId: string;
	let aliceUserId: string;
	let bobUserId: string;
	let aliceContext: BrowserContext;
	let alicePage: Page;
	let bobContext: BrowserContext;

	test.beforeAll(async ({ api, browser }) => {
		// Pull non-admin userIds from the credentials saved by global setup.
		// We need both: bob authors a row keyed to himself, admin reassigns it
		// to alice, and alice's page must show the row appear.
		const creds = loadCredentials();
		aliceUserId = creds.org1_user.userId;
		bobUserId = creds.org2_user.userId;
		expect(aliceUserId, "org1_user (alice) userId").toBeTruthy();
		expect(bobUserId, "org2_user (bob) userId").toBeTruthy();

		// 1. Create the app shell (admin context via the api fixture).
		// Explicitly scope it global (organization_id: null) so the
		// cascade scope picks it up for any org user — admin's home org
		// is PROVIDER_ORG_ID, which org1_user (alice) cannot see.
		const createApp = await api.post("/api/applications", {
			data: {
				name: VIS_APP_NAME,
				slug: VIS_APP_SLUG,
				organization_id: null,
				access_level: "authenticated",
				role_ids: [],
			},
		});
		expect(createApp.ok(), await createApp.text()).toBe(true);
		appId = (await createApp.json()).id;

		// 2. Create the table.
		// admin_bypass: lets admin perform the PATCH at the end.
		// everyone_create: lets bob (non-admin) insert the row.
		// own_user_id: gates reads on row.user_id == user.user_id — this is
		//   the row-dependent rule that flips Alice's visibility when
		//   admin reassigns the row.
		const policies = {
			policies: [
				{
					name: "admin_bypass",
					actions: ["read", "create", "update", "delete"],
					when: { user: "is_platform_admin" },
				},
				{
					name: "everyone_create",
					actions: ["create"],
					when: null,
				},
				{
					name: "own_user_id",
					actions: ["read"],
					when: { eq: [{ row: "user_id" }, { user: "user_id" }] },
				},
			],
		};
		const createTable = await api.post("/api/tables", {
			data: {
				name: VIS_TABLE_NAME,
				organization_id: null,
				policies,
			},
		});
		expect(createTable.ok(), await createTable.text()).toBe(true);
		tableId = (await createTable.json()).id;

		// 3. Seed the app source — same TSX as the realtime tripwire above.
		for (const [relPath, source] of [
			[`apps/${VIS_APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
			[`apps/${VIS_APP_SLUG}/pages/index.tsx`, INDEX_TSX],
		] as const) {
			const r = await api.post("/api/files/write", {
				data: writeBody(relPath, source),
			});
			expect(r.ok(), `write ${relPath}: ${await r.text()}`).toBe(true);
		}

		// 4. Publish the draft → live. The non-preview route
		// (/apps/{slug}/*, requireOrgUser) is the only one a non-admin can
		// reach; the /preview/* route is gated to platform admins. Publishing
		// also triggers the bundle build so the live shell can load.
		const publish = await api.post(`/api/applications/${appId}/publish`, {
			data: { message: "seed for visibility-gain test" },
		});
		expect(publish.ok(), await publish.text()).toBe(true);

		// 5. Build per-user browser contexts from the auth states global
		//    setup persisted. Alice gets a Page (she renders the app);
		//    Bob only needs an APIRequestContext (he just inserts via REST).
		const aliceState = path.resolve(
			__dirname,
			getAuthStatePath("org1_user"),
		);
		const bobState = path.resolve(
			__dirname,
			getAuthStatePath("org2_user"),
		);
		aliceContext = await browser.newContext({ storageState: aliceState });
		alicePage = await aliceContext.newPage();
		bobContext = await browser.newContext({ storageState: bobState });
	});

	test.afterAll(async ({ api }) => {
		await alicePage?.close().catch(() => {});
		await aliceContext?.close().catch(() => {});
		await bobContext?.close().catch(() => {});
		if (appId) await api.delete(`/api/applications/${appId}`);
		if (tableId) await api.delete(`/api/tables/${tableId}`);
	});

	test("admin reassignment makes a hidden row appear in alice's useTable subscription", async ({
		api,
	}) => {
		const { errors } = trackPageErrors(alicePage);

		// 1) Bob inserts a row keyed to himself. Alice should never see it
		//    via her snapshot or subscription as long as user_id stays bob.
		const bobInsert = await postAs(
			bobContext,
			`/api/tables/${tableId}/documents`,
			{ data: { value: "secret-row", user_id: bobUserId } },
		);
		expect(
			bobInsert.ok,
			`bob insert: ${bobInsert.status} ${bobInsert.text}`,
		).toBe(true);
		docId = (bobInsert.json as { id: string }).id;

		// 2) Alice opens the published app (not the preview route — that's
		//    gated to platform admins). useTable's snapshot is filtered
		//    server-side by `compile_read_filter`, so she sees zero rows.
		//    She then subscribes via websocket.
		await alicePage.goto(
			`/apps/${VIS_APP_SLUG}?table=${encodeURIComponent(tableId)}`,
		);
		await expect(alicePage.getByTestId("status")).toHaveText("ready", {
			timeout: 30_000,
		});
		await expect(alicePage.getByTestId("count")).toHaveText("0");

		// Let the websocket SUBSCRIBE handshake settle before we mutate.
		await alicePage.waitForTimeout(750);

		// 3) Admin reassigns the row. The PATCH merges `data` shallowly, so
		//    `value` stays put and `user_id` flips to alice. Server publishes
		//    document_change with old_row.user_id=bob, new_row.user_id=alice;
		//    decide_visibility_change for Alice's subscription resolves to
		//    "became_visible" → emits an INSERT to her socket.
		const patch = await api.patch(
			`/api/tables/${tableId}/documents/${docId}`,
			{ data: { data: { user_id: aliceUserId } } },
		);
		expect(patch.ok(), await patch.text()).toBe(true);

		// 4) Alice's UI must show the row. useTable applies the INSERT to
		//    local state — count goes 0 → 1 and the value text appears.
		await expect(alicePage.getByTestId("rows")).toContainText(
			"secret-row",
			{ timeout: 5000 },
		);
		await expect(alicePage.getByTestId("count")).toHaveText("1");

		expect(errors, errors.join("\n")).toEqual([]);
	});
});
