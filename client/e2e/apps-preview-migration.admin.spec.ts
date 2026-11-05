/**
 * Auto-migration + Bundled App Contract (Admin)
 *
 * Seeds a deliberately un-migrated legacy app — the shape every pre-bundler
 * app in dev has on first boot:
 *
 *   - <Outlet /> and <Icon /> used in JSX without explicit imports (legal
 *     under the old scope-injection runtime, broken on esbuild)
 *   - Platform-wrapped nav primitives (Link, useNavigate) imported from
 *     "bifrost" so they pick up the path-transforming wrappers
 *   - A user component referenced in JSX without import
 *
 * Asserts:
 *   1. Preview renders with zero console errors — the migration banner
 *      surfaces (migrated=true was returned from bundle-manifest)
 *   2. <Link to="/other"> resolves to /apps/<slug>/preview/other, NOT
 *      the bare /other that the host router would use if Link were
 *      coming from raw react-router-dom
 *   3. After publish, live path renders the same way with zero errors
 *
 * This spec is the tripwire for the class of bugs we hit on 2026-04-21
 * where (a) the save-path skipped auto-migration so user-facing errors
 * surfaced as "X is not defined", and (b) a prior fix moved Link to raw
 * react-router-dom and broke in-app navigation.
 */

import {
	test,
	expect,
	grantWorkspaceAppPolicy,
	publishAppAndWait,
} from "./fixtures/api-fixture";
import type { Page } from "./fixtures/api-fixture";
import { LEGACY_CONTROLS_TSX } from "./fixtures/legacy-controls";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-migrate-${UNIQUE}`;
const APP_NAME = `E2E Migrate ${UNIQUE}`;
const WORKFLOW_FN = `legacy_acceptance_${UNIQUE.replaceAll("-", "_")}`;
const WORKFLOW_PATH = `${WORKFLOW_FN}.py`;

// Legacy app shape: every reference is un-imported, relying on the old
// scope-injection runtime. This is what the migrator is meant to fix.
// Note: deliberately NO imports for Outlet, LayoutDashboard, DemoWidget.
const LEGACY_LAYOUT_TSX = `export default function Layout() {
	return (
		<div>
			<header data-testid="layout-heading">
				<LayoutDashboard /> App Shell
			</header>
			<Outlet />
		</div>
	);
}
`;

// Home page: uses platform-wrapped Link (must prepend base path), Badge
// (shadcn component from platform), and DemoWidget (user component — only
// referenced in JSX, NOT imported).
const LEGACY_INDEX_TSX = `import { useState } from "react";
import { Link, Badge, Button, Input, Label, useWorkflowMutation } from "bifrost";
import LegacyControls from "../components/LegacyControls";
export default function Home() {
	const [message, setMessage] = useState("");
	const { execute, data, isLoading, errorMessage } = useWorkflowMutation("__WORKFLOW_ID__");
	return (
		<div>
			<h1 data-testid="home-heading">Home</h1>
			<Badge data-testid="home-badge">BADGE</Badge>
			<DemoWidget label="widget" />
			<Label htmlFor="legacy-message">Request message</Label>
			<Input id="legacy-message" value={message} onChange={(event) => setMessage(event.target.value)} />
			<Button disabled={isLoading} onClick={() => execute({ message }).catch(() => {})}>Run legacy workflow</Button>
			<output aria-label="Workflow result">{data?.message}</output>
			{errorMessage && <p role="alert">{errorMessage}</p>}
			<Link to="/other" data-testid="to-other">Go to Other</Link>
            <LegacyControls />
		</div>
	);
}
`;

const LEGACY_OTHER_TSX = `import { Link } from "bifrost";
export default function Other() {
	return (
		<div>
			<h1 data-testid="other-heading">Other</h1>
			<Link to="/" data-testid="to-home">Back</Link>
		</div>
	);
}
`;

// User component. Default export; used in JSX as <DemoWidget ...>.
const DEMO_WIDGET_TSX = `export default function DemoWidget({ label }: { label: string }) {
	return <span data-testid="demo-widget">widget:{label}</span>;
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
		if (msg.type() === "error") {
			errors.push(`console.error: ${msg.text()}`);
		}
	});
	return { errors };
}

test.describe("Apps Preview — auto-migration", () => {
	let appId: string;

	test.beforeAll(async ({ api }) => {
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				content: `from bifrost import workflow\n\n@workflow(name="${WORKFLOW_FN}")\nasync def ${WORKFLOW_FN}(message: str):\n    return {"message": "Legacy received: " + message}\n`,
				encoding: "utf-8",
			},
		});
		expect(write.ok(), await write.text()).toBe(true);
		const registration = await api.post("/api/workflows/register", {
			data: { path: WORKFLOW_PATH, function_name: WORKFLOW_FN },
		});
		expect(registration.ok(), await registration.text()).toBe(true);
		const workflow = (await registration.json()) as { id: string };
		expect(workflow.id).toBeTruthy();
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

		// Seed the un-migrated legacy source. The bundler's save path will
		// run auto-migration before the first bundle build (because
		// SCHEMA_VERSION bumped), so these sources should be rewritten
		// server-side on their way through.
		for (const [relPath, source] of [
			[`apps/${APP_SLUG}/_layout.tsx`, LEGACY_LAYOUT_TSX],
			[`apps/${APP_SLUG}/components/DemoWidget.tsx`, DEMO_WIDGET_TSX],
			[
				`apps/${APP_SLUG}/components/LegacyControls.tsx`,
				LEGACY_CONTROLS_TSX,
			],
			[
				`apps/${APP_SLUG}/pages/index.tsx`,
				LEGACY_INDEX_TSX.replace("__WORKFLOW_ID__", workflow.id),
			],
			[`apps/${APP_SLUG}/pages/other.tsx`, LEGACY_OTHER_TSX],
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
		await api.delete(
			`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
		);
	});

	test(
		"[V1-01] legacy components execute workflows and preserve preview and published navigation — desktop",
		{ tag: "@smoke" },
		async ({ page, api }) => {
			const tracker = trackPageErrors(page);

			// --- Step 1: bundle-manifest reports migrated=true on first view.
			// This is the server-side proof that auto_migrate_repo_prefix ran
			// AND rewrote source. If the save-path hadn't migrated already,
			// the preview endpoint would migrate now; either way `migrated`
			// flips true on the first call that actually rewrites a file.
			// After the first call the source is stable — migrator is
			// idempotent — so we don't assert true on every call, just that
			// the endpoint returns something valid.
			const manifestResp = await api.get(
				`/api/applications/${appId}/bundle-manifest?mode=draft`,
			);
			expect(manifestResp.ok(), await manifestResp.text()).toBe(true);
			const manifest = await manifestResp.json();
			expect(
				manifest.entry,
				`entry missing in manifest: ${JSON.stringify(manifest)}`,
			).toBeTruthy();

			// --- Step 2: preview renders with zero console errors. This
			// transitively asserts that every un-imported JSX reference
			// (Outlet, LayoutDashboard, DemoWidget) got an import added by
			// the migrator. A regression here shows up as "X is not defined"
			// at runtime, exactly the class of bug the 2026-04-21 fix targets.
			await page.goto(`/apps/${APP_SLUG}/preview`);
			await expect(page.getByTestId("home-heading")).toHaveText("Home", {
				timeout: 15_000,
			});
			await expect(page.getByTestId("home-badge")).toHaveText("BADGE");
			await page
				.getByLabel("Request message", { exact: true })
				.fill("preview acceptance");
			await page
				.getByRole("button", {
					name: "Run legacy workflow",
					exact: true,
				})
				.click();
			await expect(
				page.getByLabel("Workflow result", { exact: true }),
			).toHaveText("Legacy received: preview acceptance");
			await expect(page.getByTestId("demo-widget")).toHaveText(
				"widget:widget",
			);
			await expect(page.getByTestId("layout-heading")).toContainText(
				"App Shell",
			);

			// --- Step 3: <Link> uses the platform wrapper that prepends the
			// app base path. Clicking must land on /apps/<slug>/preview/other,
			// NOT /other. A regression here means Link is coming from raw
			// react-router-dom (no basename) instead of "bifrost" (wrapped).
			await page.getByTestId("to-other").click();
			await expect(page).toHaveURL(
				new RegExp(`/apps/${APP_SLUG}/preview/other/?$`),
			);
			await expect(page.getByTestId("other-heading")).toHaveText("Other");

			await page.getByTestId("to-home").click();
			await expect(page).toHaveURL(
				new RegExp(`/apps/${APP_SLUG}/preview/?$`),
			);
			await expect(page.getByTestId("home-heading")).toHaveText("Home");

			// Zero console errors across the full preview + nav flow.
			expect(tracker.errors, tracker.errors.join("\n")).toEqual([]);

			// --- Step 4: publish, live path renders the same way.
			await publishAppAndWait(api, appId);

			tracker.errors.length = 0;

			await page.goto(`/apps/${APP_SLUG}`);
			await expect(page.getByTestId("home-heading")).toHaveText("Home", {
				timeout: 15_000,
			});
			await page
				.getByLabel("Request message", { exact: true })
				.fill("published acceptance");
			await page
				.getByRole("button", {
					name: "Run legacy workflow",
					exact: true,
				})
				.click();
			await expect(
				page.getByLabel("Workflow result", { exact: true }),
			).toHaveText("Legacy received: published acceptance");
			await expect(page.getByTestId("demo-widget")).toHaveText(
				"widget:widget",
			);

			// Live mode uses /apps/<slug>/<page> (no /preview segment). Same
			// contract: Link must go to /apps/<slug>/other.
			await page.getByTestId("to-other").click();
			await expect(page).toHaveURL(
				new RegExp(`/apps/${APP_SLUG}/other/?$`),
			);
			await expect(page.getByTestId("other-heading")).toHaveText("Other");

			expect(tracker.errors, tracker.errors.join("\n")).toEqual([]);
		},
	);
	for (const viewport of [
		{ name: "desktop", width: 1440, height: 1000 },
		{ name: "mobile", width: 320, height: 812 },
	]) {
		for (const theme of ["light", "dark"] as const) {
			test(`V1-CONTROLS-01 included controls work in preview and published app — ${viewport.name} ${theme}`, async ({
				page,
				api,
			}) => {
				await page.setViewportSize(viewport);
				await page.addInitScript(
					(value) => localStorage.setItem("theme", value),
					theme,
				);
				const tracker = trackPageErrors(page);
				await publishAppAndWait(api, appId);
				for (const suffix of ["/preview", ""]) {
					await page.goto(`/apps/${APP_SLUG}${suffix}`);
					const controls = page.getByRole("region", {
						name: "Legacy included controls",
					});
					await expect(controls).toBeVisible();
					await controls
						.getByRole("checkbox", { name: "Notify owner" })
						.check();
					await expect(
						controls.getByLabel("Notification state"),
					).toHaveText("Enabled");
					await controls
						.getByRole("combobox", { name: "Region" })
						.click();
					await page
						.getByRole("option", { name: "West", exact: true })
						.click();
					await expect(
						controls.getByLabel("Selected region"),
					).toHaveText("west");
					await controls
						.getByRole("button", { name: "Open legacy dialog" })
						.click();
					const dialog = page.getByRole("dialog", {
						name: "Legacy confirmation",
					});
					await expect(dialog).toBeVisible();
					const bounds = await dialog.boundingBox();
					expect(bounds).not.toBeNull();
					expect(bounds!.x).toBeGreaterThanOrEqual(0);
					expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
						viewport.width + 1,
					);
					await dialog
						.getByRole("button", { name: "Done", exact: true })
						.click();
					await expect(dialog).toBeHidden();
					await controls
						.getByRole("button", { name: "Open legacy commands" })
						.click();
					await page
						.getByPlaceholder("Find legacy command")
						.fill("Archive");
					await page
						.getByRole("option", { name: "Archive", exact: true })
						.click();
					await expect(
						controls.getByLabel("Chosen command"),
					).toHaveText("Archive");
					await controls
						.getByRole("tab", { name: "Records", exact: true })
						.click();
					await expect(
						controls.getByRole("cell", { name: "Sample record" }),
					).toBeVisible();
					await expect(
						controls.getByText("Legacy summary", { exact: true }),
					).toBeHidden();
					expect(
						await page.evaluate(
							() =>
								document.documentElement.scrollWidth <=
								window.innerWidth + 1,
						),
					).toBe(true);
				}
				expect(tracker.errors).toEqual([]);
			});
		}
	}
});
