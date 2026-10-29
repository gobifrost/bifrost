/**
 * SDK Update UI Happy Path (Admin)
 *
 * Uses dev fixture route data to keep screenshots deterministic while still
 * rendering the real Applications page/components and overflow interaction.
 * No browser polling is mocked or used; the queued durable job response drives
 * the local queued state until platform-job WebSocket updates arrive.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const NOW = "2026-09-12T12:00:00Z";
const DISPATCH_APP_ID = "11111111-1111-1111-1111-111111111111";
const ASSET_APP_ID = "22222222-2222-2222-2222-222222222222";
const PORTAL_APP_ID = "33333333-3333-3333-3333-333333333333";
const MONITOR_APP_ID = "44444444-4444-4444-4444-444444444444";
const RUNBOOK_APP_ID = "55555555-5555-5555-5555-555555555555";
const RUNBOOK_JOB_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";

function app(overrides: Record<string, unknown>) {
	return {
		id: DISPATCH_APP_ID,
		name: "Dispatch Board",
		description: "Routes technician work.",
		icon: null,
		slug: "dispatch-board",
		organization_id: null,
		published_at: NOW,
		deployed_at: NOW,
		created_at: NOW,
		updated_at: NOW,
		created_by: null,
		is_published: true,
		has_unpublished_changes: false,
		access_level: "authenticated",
		app_model: "standalone_v2",
		is_solution_managed: false,
		solution_id: null,
		role_ids: [],
		repo_path: null,
		logo: null,
		logo_url: null,
		logo_version: null,
		sdk_package_version: "1.0.0",
		sdk_fingerprint: "old",
		sdk_contract_version: 1,
		sdk_built_at: NOW,
		sdk_status: "update_available",
		sdk_source_available: true,
		...overrides,
	};
}

async function mockSdkUpdateFixtures(page: Page) {
	let sendSocketMessage: ((payload: Record<string, unknown>) => void) | undefined;
	await page.routeWebSocket(/\/ws\/connect/, (socket) => {
		sendSocketMessage = (payload) => socket.send(JSON.stringify(payload));
		socket.onMessage((raw) => {
			const payload = JSON.parse(String(raw)) as Record<string, unknown>;
			if (payload.type === "subscribe") {
				for (const channel of (payload.channels as string[]) ?? []) {
					sendSocketMessage?.({ type: "subscribed", channel });
				}
			}
			if (payload.type === "ping") {
				socket.send(JSON.stringify({ type: "pong" }));
			}
		});
	});

	await page.route("**/api/applications**", async (route) => {
		if (route.request().method() !== "GET") {
			await route.fallback();
			return;
		}
		await route.fulfill({
			json: {
				applications: [
					app({}),
					app({
						id: ASSET_APP_ID,
						name: "Asset Intake",
						slug: "asset-intake",
						description: "Source was not retained.",
						sdk_status: "update_required",
						sdk_source_available: false,
					}),
					app({
						id: PORTAL_APP_ID,
						name: "Client Portal",
						slug: "client-portal",
						description: "Already rebuilt.",
						sdk_status: "current",
						sdk_source_available: true,
					}),
					app({
						id: MONITOR_APP_ID,
						name: "Workflow Monitor",
						slug: "workflow-monitor",
						description: "SDK provenance needs a rebuild check.",
						sdk_status: "unknown",
						sdk_source_available: true,
					}),
					app({
						id: RUNBOOK_APP_ID,
						name: "Runbook Viewer",
						slug: "runbook-viewer",
						description: "Previous SDK update failed.",
						sdk_status: "update_available",
						sdk_source_available: true,
					}),
				],
				total: 5,
			},
		});
	});
	await page.route("**/api/applications/sdk/update", async (route) => {
		const body = route.request().postDataJSON() as {
			application_ids?: string[];
		};
		await route.fulfill({
			json: {
				accepted: (body.application_ids ?? []).map((applicationId) => ({
					application_id: applicationId,
					job_id:
						applicationId === DISPATCH_APP_ID
							? "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
							: applicationId === MONITOR_APP_ID
								? "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
								: RUNBOOK_JOB_ID,
					status: "queued",
					reused: false,
					notification_id: null,
				})),
				skipped: [],
			},
		});
	});
	await page.route(
		`**/api/applications/${DISPATCH_APP_ID}/sdk/update`,
		async (route) => {
			await route.fulfill({
				json: {
					job_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
					status: "queued",
					reused: false,
					notification_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
				},
			});
		},
	);
	await page.route(
		`**/api/applications/${RUNBOOK_APP_ID}/sdk/update`,
		async (route) => {
			await route.fulfill({
				json: {
					job_id: RUNBOOK_JOB_ID,
					status: "queued",
					reused: false,
					notification_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
				},
			});
		},
	);

	return {
		runMonitorUpdate() {
			if (!sendSocketMessage) {
				throw new Error("SDK update websocket did not connect");
			}
			sendSocketMessage({
				type: "platform_job_updated",
				job: {
					id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
					job_type: "application.sdk_update",
					payload_version: 1,
					resource_type: "application",
					resource_id: MONITOR_APP_ID,
					resource_lock_key: `application:${MONITOR_APP_ID}`,
					priority: 100,
					title: "Update application SDK",
					requested_by_user_id: "fixture-user",
					requested_by_name: "Fixture Admin",
					status: "running",
					progress: { current: 0, total: 1, percent: 0 },
					revision: 2,
					attempt: 1,
					max_attempts: 1,
					can_cancel: false,
					created_at: NOW,
					updated_at: NOW,
				},
			});
		},
		failRunbookUpdate() {
			if (!sendSocketMessage) {
				throw new Error("SDK update websocket did not connect");
			}
			sendSocketMessage({
				type: "platform_job_updated",
				job: {
					id: RUNBOOK_JOB_ID,
					job_type: "application.sdk_update",
					payload_version: 1,
					resource_type: "application",
					resource_id: RUNBOOK_APP_ID,
					resource_lock_key: `application:${RUNBOOK_APP_ID}`,
					priority: 100,
					title: "Update application SDK",
					requested_by_user_id: "fixture-user",
					requested_by_name: "Fixture Admin",
					status: "failed",
					progress: { current: 1, total: 1, percent: 100 },
					revision: 2,
					attempt: 1,
					max_attempts: 1,
					can_cancel: false,
					created_at: NOW,
					updated_at: NOW,
				},
			});
		},
	};
}

test.describe("Applications SDK update UI", () => {
	test("desktop supports Update All and selection-mode SDK updates", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1280, height: 900 });
		const sdkFixture = await mockSdkUpdateFixtures(page);

		await page.goto("/apps");
		await expect(
			page.getByRole("button", { name: "Update all SDKs (3)" }),
		).toBeVisible();
		// Update All covers Apps the search hides. Wait for the debounced search
		// to hide Workflow Monitor, update, then clear the search to see it.
		await page.getByLabel(/search apps/i).fill("Dispatch");
		await expect(
			page.getByRole("article").filter({ hasText: "Workflow Monitor" }),
		).toHaveCount(0);
		await page.getByRole("button", { name: "Update all SDKs (3)" }).click();
		sdkFixture.runMonitorUpdate();
		await page.getByRole("button", { name: "Clear search" }).click();
		await expect(
			page
				.getByRole("article")
				.filter({ hasText: "Workflow Monitor" })
				.getByLabel("Updating SDK"),
		).toBeVisible();
		await page.screenshot({
			path: test.info().outputPath("sdk-update-all-desktop.png"),
			fullPage: true,
		});

		await page.reload();
		await page.getByRole("button", { name: "Select" }).click();
		await page.getByRole("button", { name: "Select all" }).click();
		await expect(
			page.getByRole("button", { name: "Update selected (3)" }),
		).toBeVisible();
		await expect(
			page.getByRole("article", { name: "Asset Intake" }),
		).toHaveAttribute("aria-disabled", "true");
		await page.screenshot({
			path: test.info().outputPath("sdk-update-selection-desktop.png"),
			fullPage: true,
		});
		await page
			.getByRole("button", { name: "Update selected (3)" })
			.click();
		await expect(page.getByRole("button", { name: "Select" })).toBeVisible();
	});

	test("desktop shows update available, updating, and source-unavailable states", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1280, height: 900 });
		const sdkFixture = await mockSdkUpdateFixtures(page);

		await page.goto("/apps");
		await expect(
			page
				.getByRole("article")
				.filter({ hasText: "Dispatch Board" })
				.getByLabel("SDK update available"),
		).toBeVisible();
		await expect(page.getByLabel("SDK update required")).toBeVisible();
		await expect(page.getByLabel("SDK unknown")).toBeVisible();

		await page
			.getByRole("button", { name: "Dispatch Board actions" })
			.click();
		await page.getByRole("menuitem", { name: "Update SDK" }).click();

		await expect(page.getByLabel("SDK update queued")).toBeVisible();
		await page.keyboard.press("Escape");

		await page
			.getByRole("button", { name: "Asset Intake actions" })
			.click();
		await expect(
			page.getByRole("menuitem", {
				name: "Source unavailable — cannot rebuild SDK",
			}),
		).toHaveAttribute("aria-disabled", "true");
		await page.keyboard.press("Escape");

		await page
			.getByRole("button", { name: "Workflow Monitor actions" })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "Rebuild SDK" }),
		).toBeVisible();
		await page.keyboard.press("Escape");

		await page
			.getByRole("button", { name: "Runbook Viewer actions" })
			.click();
		await page.getByRole("menuitem", { name: "Update SDK" }).click();
		sdkFixture.failRunbookUpdate();
		await expect(page.getByLabel("SDK update failed")).toBeVisible();
		await page
			.getByRole("button", { name: "Runbook Viewer actions" })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "Retry SDK update" }),
		).toBeVisible();
		await page.screenshot({
			path: test.info().outputPath("sdk-update-failed-retry.png"),
			fullPage: true,
		});
		await page.keyboard.press("Escape");
		await page.screenshot({
			path: test.info().outputPath("sdk-update-desktop.png"),
			fullPage: true,
		});

		await page.getByRole("radio", { name: "Table view" }).click();
		await expect(page.getByRole("cell", { name: /SDK current/ })).toBeVisible();
		await expect(page.getByLabel("SDK update required")).toBeVisible();
		await expect(page.getByLabel("SDK unknown")).toBeVisible();
		await expect(page.getByLabel("Source unavailable")).toBeVisible();
		await page
			.getByRole("button", { name: "Dispatch Board actions" })
			.click();
		await expect(
			page.getByRole("menuitem", { name: "SDK update queued…" }),
		).toHaveAttribute("aria-disabled", "true");
		await page.screenshot({
			path: test.info().outputPath("sdk-update-table.png"),
			fullPage: true,
		});
	});

	test("mobile keeps SDK update affordances inside each card overflow", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await mockSdkUpdateFixtures(page);

		await page.goto("/apps");
		await expect(
			page
				.getByRole("article")
				.filter({ hasText: "Dispatch Board" })
				.getByLabel("SDK update available"),
		).toBeVisible();
		await expect(page.getByLabel("SDK update required")).toBeVisible();

		await page
			.getByRole("button", { name: "Dispatch Board actions" })
			.click();
		await page.getByRole("menuitem", { name: "Update SDK" }).click();
		await expect(page.getByLabel("SDK update queued")).toBeVisible();

		await page.screenshot({
			path: test.info().outputPath("sdk-update-mobile.png"),
			fullPage: true,
		});
	});
});
