import { expect, test } from "@playwright/test";

const NOW = "2026-08-16T12:00:00Z";
const SOLUTION_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";
const CONVERSATION_ID = "33333333-3333-4333-8333-333333333333";
const USER_ID = "44444444-4444-4444-8444-444444444444";
const AGENT_ID = "55555555-5555-4555-8555-555555555555";
const TURN_ID = "66666666-6666-4666-8666-666666666666";

const solution = {
	id: SOLUTION_ID,
	slug: "customer-intake",
	name: "Customer Intake",
	visibility: "private",
	owner_user_id: USER_ID,
	owner_name: "Platform Admin",
	owner_email: "admin@example.com",
	organization_id: null,
	organization_name: "MSP Workspace",
	caller_access: "owner",
	collaborator_access: null,
	status: "active",
	target_kind: "solution",
	promotion_status: "none",
	created_at: NOW,
	updated_at: NOW,
};

test.describe("Code Builder", () => {
	test("guides an administrator through Builder setup before access is enabled", async ({
		page,
	}) => {
		await page.route("**/api/builder/solutions**", async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			if (
				request.method() === "GET" &&
				url.pathname === "/api/builder/solutions"
			) {
				await route.fulfill({
					json: {
						solutions: [],
						total: 0,
						limit: null,
						offset: 0,
						view: "mine",
						can_view_all: true,
						ai_configured: false,
						builder_ready: false,
						builder_blockers: [
							{
								code: "ai_not_configured",
								message: "Connect an AI provider and model.",
								action: "Choose and test a model in AI settings.",
							},
							{
								code: "runner_not_ready",
								message: "Connect an isolated Builder runner.",
								action: "Provision the local or Cloudflare runner.",
							},
						],
						is_platform_admin: true,
					},
				});
				return;
			}
			await route.fulfill({ status: 404, json: { detail: "Not found" } });
		});

		await page.goto("/build");
		await expect(
			page.getByRole("heading", { name: "Finish connecting Builder" }),
		).toBeVisible();
		await expect(page.getByText("Connect an AI provider and model.")).toBeVisible();
		await expect(
			page.getByText("Connect an isolated Builder runner."),
		).toBeVisible();
		await page.getByRole("button", { name: "Open Builder setup" }).click();
		await expect(page).toHaveURL("/settings/builder");
	});

	test("creates a private app and opens the shared agent workbench", async ({
		page,
	}, testInfo) => {
		let submittedPrompt: Record<string, unknown> | null = null;

		await page.route("**/api/builder/solutions**", async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			const path = url.pathname;

			if (
				path === "/api/builder/solutions" &&
				request.method() === "GET"
			) {
				await route.fulfill({
					json: {
						solutions: [],
						total: 0,
						limit: null,
						offset: 0,
						view: "mine",
						can_view_all: true,
						ai_configured: true,
						builder_ready: true,
						builder_blockers: [],
						is_platform_admin: true,
					},
				});
				return;
			}

			if (path === "/api/builder/solutions/global-workspace") {
				await route.fulfill({
					json: {
						exists: false,
						solution_id: null,
						current_revision_id: null,
						deployed_revision_id: null,
						has_pending_proposal: false,
						can_rollback: false,
						last_applied_at: null,
					},
				});
				return;
			}

			if (
				path === "/api/builder/solutions" &&
				request.method() === "POST"
			) {
				await route.fulfill({ json: solution });
				return;
			}

			if (path === `/api/builder/solutions/${SOLUTION_ID}`) {
				await route.fulfill({ json: solution });
				return;
			}

			if (path === `/api/builder/solutions/${SOLUTION_ID}/sessions`) {
				const session = {
					id: SESSION_ID,
					solution_id: SOLUTION_ID,
					conversation_id: CONVERSATION_ID,
					user_id: USER_ID,
					builder_agent_id: AGENT_ID,
					created_at: NOW,
					updated_at: NOW,
				};
				if (request.method() === "POST") {
					await new Promise((resolve) => setTimeout(resolve, 250));
					await route.fulfill({ json: session });
				} else {
					await route.fulfill({
						json: { sessions: [session], total: 1 },
					});
				}
				return;
			}

			if (path === `/api/builder/solutions/${SOLUTION_ID}/revisions`) {
				await route.fulfill({ json: { revisions: [], total: 0 } });
				return;
			}

			if (path === `/api/builder/solutions/${SOLUTION_ID}/turns`) {
				if (request.method() === "POST") {
					submittedPrompt = request.postDataJSON() as Record<
						string,
						unknown
					>;
					await route.fulfill({
						status: 202,
						json: {
							job_id: TURN_ID,
							status: "queued",
							turn: {
								id: TURN_ID,
								session_id: SESSION_ID,
								requested_by: USER_ID,
								base_revision_id: null,
								output_revision_id: null,
								resume_from_turn_id: null,
								checkpoint_available: false,
								build_job_id: null,
								deploy_job_id: null,
								status: "queued",
								error: null,
								created_at: NOW,
								started_at: null,
								completed_at: null,
							},
						},
					});
				} else {
					await route.fulfill({ json: { turns: [], total: 0 } });
				}
				return;
			}

			await route.fulfill({
				status: 404,
				json: { detail: "Unmocked Builder route" },
			});
		});

		await page.route("**/api/applications**", (route) =>
			route.fulfill({ json: { applications: [], total: 0 } }),
		);
		await page.route("**/api/chat/model-tiers", (route) =>
			route.fulfill({
				json: {
					default_tier: "balanced",
					tiers: [
						{
							id: "balanced",
							label: "Balanced",
							capabilities: {
								image_input: false,
								pdf_input: false,
								tool_calling: true,
								source: "configured",
								fingerprint: "builder-e2e",
							},
						},
					],
				},
			}),
		);
		await page.route(
			`**/api/chat/conversations/${CONVERSATION_ID}/messages`,
			(route) => route.fulfill({ json: [] }),
		);
		await page.route(`**/api/platform-jobs/${TURN_ID}`, (route) =>
			route.fulfill({
				json: {
					id: TURN_ID,
					job_type: "solution.builder.turn",
					payload_version: 1,
					organization_id: null,
					resource_type: "solution_builder_turn",
					resource_id: TURN_ID,
					resource_lock_key: null,
					priority: 100,
					title: "Building Customer Intake",
					action_url: `/solutions/${SOLUTION_ID}/builder`,
					requested_by_user_id: USER_ID,
					requested_by_name: "Platform Admin",
					status: "running",
					progress: {
						phase: "Starting Builder workspace",
						current: 0,
						total: null,
						percent: 5,
					},
					revision: 1,
					attempt: 1,
					max_attempts: 2,
					can_cancel: true,
					result: null,
					error: null,
					notification_id: null,
					external_provider: "local",
					external_run_id: TURN_ID,
					external_started_at: NOW,
					started_at: NOW,
					completed_at: null,
					created_at: NOW,
					updated_at: NOW,
				},
			}),
		);

		await page.goto("/build");
		await expect(
			page.getByRole("heading", { name: "What should Bifrost build?" }),
		).toBeVisible();
		await page.getByLabel("App name").fill("Customer Intake");
		await page
			.getByLabel("Describe your app")
			.fill("Build a customer intake app with a review queue.");
		await page.getByRole("button", { name: "Start building" }).click();

		await expect(
			page.getByText("Starting the Builder Agent"),
		).toBeVisible();
		await expect(page).toHaveURL(`/solutions/${SOLUTION_ID}/builder`);
		await expect(
			page.getByRole("heading", { name: "Customer Intake" }),
		).toBeVisible();
		await expect(page.getByTestId("active-builder-skill")).toHaveText(
			"bifrost-build",
		);
		await expect(
			page.getByRole("button", { name: /Use your AI/i }),
		).toBeVisible();
		await expect(page.getByRole("tab", { name: "Preview" })).toBeVisible();
		await expect(page.getByRole("tab", { name: "Code" })).toBeVisible();
		await expect(page.getByRole("tab", { name: "Changes" })).toBeVisible();
		await expect
			.poll(() => submittedPrompt)
			.toMatchObject({
				session_id: SESSION_ID,
				message: "Build a customer intake app with a review queue.",
				attachment_ids: [],
			});

		await testInfo.attach("Builder — active workbench", {
			body: await page.screenshot({ fullPage: true }),
			contentType: "image/png",
		});
	});

	test("keeps the restored Builder workbench usable on mobile", async ({
		page,
	}) => {
		const session = {
			id: SESSION_ID,
			solution_id: SOLUTION_ID,
			conversation_id: CONVERSATION_ID,
			user_id: USER_ID,
			builder_agent_id: AGENT_ID,
			created_at: NOW,
			updated_at: NOW,
		};

		await page.route("**/api/builder/solutions**", async (route) => {
			const request = route.request();
			const path = new URL(request.url()).pathname;
			if (path === `/api/builder/solutions/${SOLUTION_ID}`) {
				await route.fulfill({ json: solution });
				return;
			}
			if (path === `/api/builder/solutions/${SOLUTION_ID}/sessions`) {
				await route.fulfill({ json: { sessions: [session], total: 1 } });
				return;
			}
			if (path === `/api/builder/solutions/${SOLUTION_ID}/revisions`) {
				await route.fulfill({ json: { revisions: [], total: 0 } });
				return;
			}
			if (path === `/api/builder/solutions/${SOLUTION_ID}/turns`) {
				await route.fulfill({ json: { turns: [], total: 0 } });
				return;
			}
			await route.fulfill({
				status: 404,
				json: { detail: "Unmocked Builder route" },
			});
		});
		await page.route("**/api/applications**", (route) =>
			route.fulfill({ json: { applications: [], total: 0 } }),
		);
		await page.route("**/api/chat/model-tiers", (route) =>
			route.fulfill({
				json: {
					default_tier: "balanced",
					tiers: [
						{
							id: "balanced",
							label: "Balanced",
							capabilities: {
								image_input: false,
								pdf_input: false,
								tool_calling: true,
								source: "configured",
								fingerprint: "builder-mobile-e2e",
							},
						},
					],
				},
			}),
		);
		await page.route(
			`**/api/chat/conversations/${CONVERSATION_ID}/messages`,
			(route) => route.fulfill({ json: [] }),
		);

		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/solutions/${SOLUTION_ID}/builder`);
		await expect(
			page.getByRole("heading", { name: "Customer Intake" }),
		).toBeVisible();
		for (const label of ["Agent", "Preview", "Code", "Changes"]) {
			await expect(
				page.getByRole("button", { name: label, exact: true }),
			).toBeVisible();
		}

		await page.getByRole("button", { name: "Code", exact: true }).click();
		await expect(page.getByText("No source revision is available yet.")).toBeVisible();
		await page.reload();
		await expect(page.getByText("No source revision is available yet.")).toBeVisible();

		await page.getByRole("button", { name: "Changes", exact: true }).click();
		await expect(page.getByText("Revision history")).toBeVisible();
		await page.getByRole("button", { name: "Preview", exact: true }).click();
		await expect(page.getByText("Preview is not deployed yet")).toBeVisible();
		await page.getByRole("button", { name: "Agent", exact: true }).click();
		await expect(
			page.getByText("bifrost-build guides each generated change"),
		).toBeVisible();
	});
});
