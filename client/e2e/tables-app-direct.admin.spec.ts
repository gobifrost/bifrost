import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-tables-sdk-${UNIQUE}`;
const APP_NAME = `E2E Tables SDK ${UNIQUE}`;
const TABLE_NAME = `e2e_tables_sdk_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

const INDEX_TSX = `import { tables, useState } from "bifrost";

export default function Home() {
  const [last, setLast] = useState<string>("idle");
  const [rows, setRows] = useState<unknown[]>([]);

  async function onInsert() {
    try {
      const doc = await tables.insert("${TABLE_NAME}", { value: "from-app" });
      setLast(\`inserted:\${doc.id}\`);
    } catch (e) {
      setLast(\`error:\${(e as Error).message}\`);
    }
  }

  async function onQuery() {
    const r = await tables.query("${TABLE_NAME}");
    setRows(r.documents);
    setLast(\`queried:\${r.documents.length}\`);
  }

  return (
    <div>
      <button data-testid="insert" onClick={onInsert}>Insert</button>
      <button data-testid="query" onClick={onQuery}>Query</button>
      <div data-testid="last">{last}</div>
      <ul data-testid="rows">
        {rows.map((r: any) => <li key={r.id}>{r.data?.value}</li>)}
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

test.describe("Tables web SDK in apps", () => {
	let appId: string;
	let tableId: string;

	test.beforeAll(async ({ api }) => {
		// Create the app
		const createApp = await api.post("/api/applications", {
			data: { name: APP_NAME, slug: APP_SLUG, access_level: "authenticated", role_ids: [] },
		});
		expect(createApp.ok(), await createApp.text()).toBe(true);
		appId = (await createApp.json()).id;

		// Create the table with everyone.read+create
		const createTable = await api.post("/api/tables", { data: { name: TABLE_NAME } });
		expect(createTable.ok(), await createTable.text()).toBe(true);
		tableId = (await createTable.json()).id;
		const setAccess = await api.patch(`/api/tables/${tableId}`, {
			data: {
				access: {
					everyone: { read: true, create: true, update: false, delete: false },
					role: { roles: [], read: false, create: false, update: false, delete: false },
					creator: { read: false, create: false, update: false, delete: false },
				},
			},
		});
		expect(setAccess.ok(), await setAccess.text()).toBe(true);

		// Seed app source
		for (const [relPath, source] of [
			[`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
			[`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
		] as const) {
			const r = await api.post("/api/files/write", { data: writeBody(relPath, source) });
			expect(r.ok(), await r.text()).toBe(true);
		}
	});

	test.afterAll(async ({ api }) => {
		await api.delete(`/api/applications/${appId}`);
		await api.delete(`/api/tables/${tableId}`);
	});

	test("app inserts a row, queries, and renders results — no workflow execution created", async ({ page, api }) => {
		const { errors } = trackPageErrors(page);

		// Capture the count of executions before the test
		const before = await (await api.get("/api/executions?limit=50")).json();
		const beforeCount = before.executions?.length ?? 0;

		await page.goto(`/apps/${APP_SLUG}/preview`);
		await expect(page.locator('[data-testid="insert"]')).toBeVisible({ timeout: 30_000 });
		await page.click('[data-testid="insert"]');
		await expect(page.locator('[data-testid="last"]')).toContainText("inserted:");

		await page.click('[data-testid="query"]');
		await expect(page.locator('[data-testid="last"]')).toContainText("queried:1");
		await expect(page.locator('[data-testid="rows"]')).toContainText("from-app");

		// Confirm no new execution was created (app went over REST, not workflows)
		const after = await (await api.get("/api/executions?limit=50")).json();
		expect((after.executions?.length ?? 0)).toBe(beforeCount);

		expect(errors).toEqual([]);
	});
});
