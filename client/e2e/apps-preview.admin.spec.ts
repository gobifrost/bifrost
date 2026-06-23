/**
 * Apps Preview Happy Path (Admin)
 *
 * End-to-end contract for push → preview → publish, plus cross-page
 * navigation inside the bundled app. Seeds a minimal self-contained
 * app, navigates to the preview, pushes a source change, and asserts
 * the new content appears WITHOUT a hard refresh. Then exercises
 * <Link>-driven navigation to a second page and back, then publishes
 * and confirms the live path reflects the final state.
 *
 * Tripwire for the CLI → /api/files/write → bundler → S3 → Redis
 * pubsub → WebSocket → browser dynamic import pipeline, plus the
 * app-internal react-router wiring the bundler emits in _entry.tsx.
 */

import { test, expect, grantWorkspaceAppPolicy } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-preview-${UNIQUE}`;
const APP_NAME = `E2E Preview ${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() {
	return <Outlet />;
}
`;

const indexTsx = (heading: string) => `import { Link } from "bifrost";
import { useLocation } from "react-router-dom";
export default function Home() {
	const location = useLocation();
	return (
		<div>
			<h1 data-testid="demo-heading">${heading}</h1>
			<div data-testid="location-path">{location.pathname}</div>
			<Link to="/other" data-testid="to-other">Go to Other</Link>
		</div>
	);
}
`;

const OTHER_TSX = `import { Link } from "bifrost";
import { useLocation } from "react-router-dom";
export default function Other() {
	const location = useLocation();
	return (
		<div>
			<h1 data-testid="other-heading">OTHER PAGE</h1>
			<div data-testid="location-path">{location.pathname}</div>
			<Link to="/" data-testid="to-home">Back to Home</Link>
		</div>
	);
}
`;

// Matches the CLI's /api/files/write contract (see api/bifrost/cli.py).
function writeBody(path: string, content: string) {
	return {
		path,
		content: Buffer.from(content, "utf-8").toString("base64"),
		mode: "cloud",
		location: "workspace",
		binary: true,
	};
}

// Collect page errors and console errors so we can assert the preview renders
// cleanly. The bundler path has historically swallowed errors to the browser
// console; treat any of those as a test failure.
function trackPageErrors(page: Page): { errors: string[] } {
	const errors: string[] = [];
	page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
	page.on("console", (msg) => {
		if (msg.type() === "error") {
			errors.push(`console.error: ${msg.text()}`);
		}
	});
	return { errors };
}

test.describe("Apps Preview", () => {
	let appId: string;

	test.beforeAll(async ({ api }) => {
		const createResp = await api.post("/api/applications", {
			data: {
				name: APP_NAME,
				slug: APP_SLUG,
				access_level: "authenticated",
				role_ids: [],
				// `POST /api/applications` now defaults to standalone_v2 (which requires
				// a Solution deploy); pin the legacy inline model this suite relies on.
				app_model: "inline_v1",
			},
		});
		expect(createResp.ok(), await createResp.text()).toBe(true);
		const app = await createResp.json();
		appId = app.id;
		await grantWorkspaceAppPolicy(api, APP_SLUG);

		// Seed the minimum files the bundler needs: a layout, a home page,
		// and a second page to exercise in-app navigation.
		for (const [relPath, source] of [
			[`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
			[`apps/${APP_SLUG}/pages/index.tsx`, indexTsx("HELLO V1")],
			[`apps/${APP_SLUG}/pages/other.tsx`, OTHER_TSX],
		] as const) {
			const writeResp = await api.post("/api/files/write", {
				data: writeBody(relPath, source),
			});
			expect(
				writeResp.ok(),
				`write ${relPath}: ${await writeResp.text()}`,
			).toBe(true);
		}
	});

	test.afterAll(async ({ api }) => {
		if (appId) await api.delete(`/api/applications/${appId}`);
	});

	test("hot-reloads preview on push, navigates pages, and publishes to live", async ({
		page,
		api,
	}) => {
		const tracker = trackPageErrors(page);

		// --- Step 1: preview shows V1 ---
		await page.goto(`/apps/${APP_SLUG}/preview`);
		await expect(page.getByTestId("demo-heading")).toHaveText("HELLO V1", {
			timeout: 15_000,
		});
		await expect(page.getByTestId("location-path")).toHaveText("/");

		// --- Step 2: push V2, preview updates WITHOUT reload ---
		const writeResp = await api.post("/api/files/write", {
			data: writeBody(
				`apps/${APP_SLUG}/pages/index.tsx`,
				indexTsx("HELLO V2"),
			),
		});
		expect(writeResp.ok(), await writeResp.text()).toBe(true);

		await expect(page.getByTestId("demo-heading")).toHaveText("HELLO V2", {
			timeout: 15_000,
		});
		await expect(page.getByTestId("location-path")).toHaveText("/");

		// --- Step 3: navigate to /other via in-app Link, then back ---
		await page.getByTestId("to-other").click();
		await expect(page).toHaveURL(
			new RegExp(`/apps/${APP_SLUG}/preview/other/?$`),
		);
		await expect(page.getByTestId("other-heading")).toHaveText(
			"OTHER PAGE",
		);
		await expect(page.getByTestId("location-path")).toHaveText("/other");

		await page.getByTestId("to-home").click();
		await expect(page).toHaveURL(
			new RegExp(`/apps/${APP_SLUG}/preview/?$`),
		);
		await expect(page.getByTestId("demo-heading")).toHaveText("HELLO V2");
		await expect(page.getByTestId("location-path")).toHaveText("/");

		// No console errors / pageerrors during the hot-reload + navigation
		// flow. This also catches "provider context missing" regressions, which
		// surface as console.error from React but don't throw.
		expect(tracker.errors, tracker.errors.join("\n")).toEqual([]);

		// --- Step 4: publish, live path shows V2 ---
		const pubResp = await api.post(`/api/applications/${appId}/publish`);
		expect(pubResp.ok(), await pubResp.text()).toBe(true);

		// Reset the tracker before the next navigation so prior-step noise
		// doesn't cross-contaminate the live-path assertion.
		tracker.errors.length = 0;

		await page.goto(`/apps/${APP_SLUG}`);
		await expect(page.getByTestId("demo-heading")).toHaveText("HELLO V2", {
			timeout: 15_000,
		});
		await expect(page.getByTestId("location-path")).toHaveText("/");

		// Also verify navigation works in live mode.
		await page.getByTestId("to-other").click();
		await expect(page).toHaveURL(
			new RegExp(`/apps/${APP_SLUG}/other/?$`),
		);
		await expect(page.getByTestId("other-heading")).toHaveText(
			"OTHER PAGE",
		);
		await expect(page.getByTestId("location-path")).toHaveText("/other");

		expect(tracker.errors, tracker.errors.join("\n")).toEqual([]);
	});
});
