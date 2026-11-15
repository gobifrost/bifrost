/**
 * Cloudflare Capacity Settings Happy Path (Admin)
 *
 * The Cloudflare provider never receives a real credential in this test. The
 * API and durable job transports are intercepted so the test still exercises
 * the real Settings flow, including its write-only credential behavior.
 */

import { expect, test } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

const NOW = "2026-11-14T12:00:00Z";
const JOB_ID = "11111111-1111-1111-1111-111111111111";
const NOTIFICATION_ID = "22222222-2222-2222-2222-222222222222";
const FAKE_API_KEY = "cloudflare-test-key-not-a-secret";
const PUBLIC_ORIGIN = "https://bifrost.example.test";

type CloudflareCapacityStatus =
	components["schemas"]["CloudflareCapacityStatus"];
type CloudflareCapacityRules = components["schemas"]["CloudflareCapacityRules"];
type CloudflareCapacityConnectAccepted =
	components["schemas"]["CloudflareCapacityConnectAccepted"];
type CloudflareCapacityConnectRequest =
	components["schemas"]["CloudflareCapacityConnectRequest"];
type PlatformJob = components["schemas"]["PlatformJobPublic"];
type Notification = components["schemas"]["NotificationPublic"];
type PoolsListResponse = components["schemas"]["PoolsListResponse"];

const DEFAULT_RULES: CloudflareCapacityRules = {
	workflows: true,
	agents: true,
	app_builds: true,
	prefer_kubernetes: false,
};

function cloudflareStatus(
	overrides: Partial<CloudflareCapacityStatus> = {},
): CloudflareCapacityStatus {
	return {
		configured: false,
		active: false,
		status: "unconfigured",
		rules: DEFAULT_RULES,
		...overrides,
	};
}

function platformJob(
	status: PlatformJob["status"],
	phase: string,
): PlatformJob {
	return {
		id: JOB_ID,
		job_type: "cloudflare.capacity.apply",
		payload_version: 1,
		resource_type: "worker_capacity",
		resource_id: "cloudflare",
		resource_lock_key: "worker_capacity:cloudflare",
		priority: 100,
		title: "Set up Cloudflare capacity",
		execution_backend: "local",
		requested_by_user_id: "fixture-user",
		requested_by_name: "Fixture Admin",
		status,
		progress: {
			phase,
			current: status === "succeeded" ? 1 : 0,
			total: 1,
			percent: status === "succeeded" ? 100 : 0,
		},
		revision: status === "succeeded" ? 2 : 1,
		attempt: 1,
		max_attempts: 1,
		can_cancel: false,
		created_at: NOW,
		updated_at: NOW,
	};
}

test("an admin connects Cloudflare capacity and saves start rules", async ({
	page,
}, testInfo) => {
	let sendSocketMessage: ((payload: Record<string, unknown>) => void) | undefined;
	let capacity = cloudflareStatus();
	const credentialRequests: string[] = [];

	page.on("request", (request) => {
		if (request.postData()?.includes(FAKE_API_KEY)) {
			credentialRequests.push(
				`${request.method()} ${new URL(request.url()).pathname}`,
			);
		}
	});

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

	const workers: PoolsListResponse = {
		pools: [],
		total: 0,
		capacity_overview: {
			global_managed_limit: 10,
			managed_in_use: 0,
			cloudflare_profile_limit: null,
			cleanup_pending_count: 0,
			forms: [],
		},
	};
	await page.route("**/api/platform/workers**", async (route) => {
		await route.fulfill({ json: workers });
	});

	await page.route("**/api/admin/worker-capacity/cloudflare", async (route) => {
		if (route.request().method() === "GET") {
			await route.fulfill({ json: capacity });
			return;
		}
		if (route.request().method() === "POST") {
			const request = route.request().postDataJSON() as CloudflareCapacityConnectRequest;
			expect(request).toEqual({
				api_key: FAKE_API_KEY,
				bifrost_api_origin: PUBLIC_ORIGIN,
				max_workers: 2,
			});
			const connection = cloudflareStatus({
				configured: true,
				status: "provisioning",
				last_job_id: JOB_ID,
				max_workers: 2,
			});
			const accepted: CloudflareCapacityConnectAccepted = {
				job_id: JOB_ID,
				connection,
			};
			await route.fulfill({ status: 202, json: accepted });
			return;
		}
		await route.fallback();
	});

	await page.route("**/api/platform-jobs/11111111-1111-1111-1111-111111111111", async (route) => {
		await route.fulfill({ json: platformJob("running", "Preparing Cloudflare") });
	});

	await page.route("**/api/admin/worker-capacity/cloudflare/rules", async (route) => {
		expect(route.request().method()).toBe("PUT");
		const rules = route.request().postDataJSON() as CloudflareCapacityRules;
		expect(rules).toEqual({ ...DEFAULT_RULES, agents: false });
		capacity = cloudflareStatus({
			configured: true,
			active: true,
			status: "ready",
			endpoint: "https://workers.cloudflare.com",
			scope: "account",
			worker_version: "2026.11.14",
			max_workers: 2,
			memory_limit_bytes: 4 * 1024 ** 3,
			rules,
		});
		await route.fulfill({ json: capacity });
	});

	await page.goto("/settings/ai");
	await page.getByRole("button", { name: "Workers", exact: true }).click();
	await page
		.getByRole("button", { name: "Scaling & Isolation", exact: true })
		.click();

	const apiKey = page.getByLabel("Cloudflare API Key");
	await expect(apiKey).toBeVisible();
	await apiKey.fill(FAKE_API_KEY);
	await page.getByLabel("Bifrost Public API URL").fill(PUBLIC_ORIGIN);

	const [connected] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/admin/worker-capacity/cloudflare") &&
				response.request().method() === "POST",
		),
		page.getByRole("button", { name: "Connect Cloudflare", exact: true }).click(),
	]);
	expect(connected.status()).toBe(202);
	expect(credentialRequests).toEqual([
		"POST /api/admin/worker-capacity/cloudflare",
	]);
	await expect(apiKey).toBeHidden();
	await expect(page.getByText("Setup In Progress", { exact: true })).toBeVisible();

	capacity = cloudflareStatus({
		configured: true,
		active: true,
		status: "ready",
		endpoint: "https://workers.cloudflare.com",
		scope: "account",
		worker_version: "2026.11.14",
		max_workers: 2,
		memory_limit_bytes: 4 * 1024 ** 3,
		rules: DEFAULT_RULES,
	});
	if (!sendSocketMessage) {
		throw new Error("Cloudflare setup websocket did not connect");
	}
	const notification: Notification = {
		id: NOTIFICATION_ID,
		category: "system",
		title: "Cloudflare capacity ready",
		description: "Cloudflare setup completed.",
		status: "succeeded",
		percent: 100,
		error: null,
		result: null,
		metadata: { job_id: JOB_ID },
		created_at: NOW,
		updated_at: NOW,
		user_id: "fixture-user",
	};
	sendSocketMessage({ type: "notification_updated", notification });
	sendSocketMessage({
		type: "platform_job_updated",
		job: platformJob("succeeded", "Cloudflare setup completed"),
	});

	await expect(page.getByText("Provider Ready", { exact: true })).toBeVisible();
	await expect(page.getByLabel("Replace Cloudflare API Key")).toHaveValue("");
	await expect(
		page.getByText("Cloudflare has verified this provider is ready."),
	).toBeVisible();
	await expect(page.getByText("4 GiB per worker (fixed)")).toBeVisible();
	await expect(page.getByText("2026.11.14", { exact: true })).toBeVisible();

	const agents = page.getByRole("switch", { name: "Run Agents and Summaries" });
	await expect(agents).toBeChecked();
	await agents.click();
	const [savedRules] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/admin/worker-capacity/cloudflare/rules") &&
				response.request().method() === "PUT",
		),
		page.getByRole("button", { name: "Save Cloudflare Rules", exact: true }).click(),
	]);
	expect(savedRules.ok(), await savedRules.text()).toBeTruthy();
	await expect(agents).not.toBeChecked();
	await page.screenshot({ path: testInfo.outputPath("cloudflare-capacity-desktop.png"), fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(page.getByText("Cloudflare Workers", { exact: true })).toBeVisible();
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth <= window.innerWidth,
		),
	).toBe(true);
	await page.screenshot({ path: testInfo.outputPath("cloudflare-capacity-mobile.png"), fullPage: true });
});
