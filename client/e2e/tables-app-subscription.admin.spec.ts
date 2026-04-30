import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-tables-sub-${UNIQUE}`;
const APP_NAME = `E2E Tables Sub ${UNIQUE}`;
const TABLE_NAME = `e2e_tables_sub_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

// The spec passes the table_id in via search params so the seeded source can
// stay parameter-free. The app reads it on mount via useSearchParams.
// useSearchParams is routed through react-router-dom by the bundler, so it
// returns the raw react-router tuple [URLSearchParams, setter].
const INDEX_TSX = `import { useTableSubscription, useState, useSearchParams } from "bifrost";

export default function Home() {
  const [params] = useSearchParams();
  const tableId = params.get("table") ?? "";
  const [events, setEvents] = useState<string[]>([]);

  useTableSubscription(tableId, (evt: any) => {
    setEvents((prev) => [...prev, \`\${evt.type}:\${evt.action ?? ""}\`]);
  });

  return (
    <div>
      <div data-testid="ready">ready</div>
      <ul data-testid="events">
        {events.map((e, i) => <li key={i}>{e}</li>)}
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

test.describe("Tables subscription in apps", () => {
	let appId: string;
	let tableId: string;

	test.beforeAll(async ({ api }) => {
		const createApp = await api.post("/api/applications", {
			data: { name: APP_NAME, slug: APP_SLUG, access_level: "authenticated", role_ids: [] },
		});
		expect(createApp.ok(), await createApp.text()).toBe(true);
		appId = (await createApp.json()).id;

		const createTable = await api.post("/api/tables", { data: { name: TABLE_NAME } });
		expect(createTable.ok(), await createTable.text()).toBe(true);
		tableId = (await createTable.json()).id;
		await api.patch(`/api/tables/${tableId}`, {
			data: {
				access: {
					everyone: { read: true, create: true, update: false, delete: false },
					role: { roles: [], read: false, create: false, update: false, delete: false },
					creator: { read: false, create: false, update: false, delete: false },
				},
			},
		});

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

	test("app receives a push event when a row is inserted via REST", async ({ page, api }) => {
		const { errors } = trackPageErrors(page);

		await page.goto(`/apps/${APP_SLUG}/preview?table=${tableId}`);
		await expect(page.locator('[data-testid="ready"]')).toBeVisible({ timeout: 15_000 });
		// Give the ws a moment to subscribe before the REST insert fires.
		await page.waitForTimeout(500);

		await api.post(`/api/tables/${tableId}/documents`, { data: { data: { x: 1 } } });

		await expect(page.locator('[data-testid="events"]')).toContainText("document_change:insert", { timeout: 5000 });

		expect(errors).toEqual([]);
	});
});
