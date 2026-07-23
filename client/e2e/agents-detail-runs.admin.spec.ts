/**
 * Agent Detail — Runs Tab (Admin)
 *
 * Smoke tests for the per-agent detail page (`/agents/:id`):
 *   - Edit mode renders Overview/Runs/Settings tabs and the Runs tab opens.
 *   - Create mode (`/agents/new`) disables Overview/Runs and only Settings
 *     is interactive.
 *
 * Uses the api fixture to create + clean up an agent so the detail page
 * has deterministic state to render against.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

function makeRun(
	agentId: string,
	index: number,
	parentRunId: string | null = null,
) {
	const createdAt = new Date(Date.now() - index * 60_000).toISOString();
	return {
		id: `90000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
		agent_id: agentId,
		agent_name: "Viewport Test Agent",
		trigger_type: "manual",
		trigger_source: "Playwright",
		conversation_id: null,
		event_delivery_id: null,
		input: { ticket_id: String(430000 + index) },
		output: { routed: true },
		status: "completed",
		error: null,
		org_id: null,
		caller_user_id: null,
		caller_email: null,
		caller_name: "E2E Operator",
		iterations_used: 4,
		tokens_used: 20_000 + index * 100,
		budget_max_iterations: 50,
		budget_max_tokens: 100_000,
		duration_ms: 18_000 + index * 250,
		llm_model: "gpt-5.2",
		asked: `Mock run ${index}: triage ticket ${430000 + index}`,
		did: "Reviewed the ticket, validated the client context, and routed it to the correct queue.",
		answered: "The ticket was triaged successfully.",
		metadata: {
			ticket_id: String(430000 + index),
			client: index % 2 ? "Northwind Services" : "Contoso",
			impact: "Single User",
		},
		confidence: 0.92,
		confidence_reason: "The ticket fields agreed.",
		summary_status: "completed",
		summary_error: null,
		verdict: null,
		verdict_note: null,
		verdict_set_at: null,
		verdict_set_by: null,
		created_at: createdAt,
		started_at: createdAt,
		completed_at: createdAt,
		parent_run_id: parentRunId,
	};
}

async function mockRunHistory(page: Page, agentId: string, count = 24) {
	const runs = Array.from({ length: count }, (_, index) =>
		makeRun(agentId, index + 1),
	);
	runs[0] = {
		...runs[0],
		trigger_type: "delegation",
		parent_run_id: "80000000-0000-4000-8000-000000000001",
	};

	await page.route("**/api/agent-runs*", async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		if (
			request.method() !== "GET" ||
			url.pathname !== "/api/agent-runs" ||
			url.searchParams.get("agent_id") !== agentId
		) {
			await route.fallback();
			return;
		}

		const limit = Number(url.searchParams.get("limit") ?? 50);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const nextOffset = offset + limit;
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				items: runs.slice(offset, nextOffset),
				total: runs.length,
				next_cursor: nextOffset < runs.length ? nextOffset : null,
			}),
		});
	});
}

async function mockHierarchicalRun(page: Page, agentId: string) {
	const parentId = "91000000-0000-4000-8000-000000000001";
	const childId = "91000000-0000-4000-8000-000000000002";
	const grandchildId = "91000000-0000-4000-8000-000000000003";
	const createdAt = new Date().toISOString();
	const step = (
		runId: string,
		stepNumber: number,
		type: string,
		content: Record<string, unknown>,
	) => ({
		id: `${runId.slice(0, 24)}${String(stepNumber).padStart(12, "0")}`,
		run_id: runId,
		step_number: stepNumber,
		type,
		content,
		tokens_used: type.startsWith("llm") ? 2100 : null,
		duration_ms: stepNumber * 900,
		created_at: createdAt,
	});
	const detail = (
		id: string,
		agentName: string,
		asked: string,
		did: string,
		steps: ReturnType<typeof step>[],
		childRunIds: string[],
		childRuns: Array<Record<string, unknown>> = [],
	) => ({
		...makeRun(agentId, 1),
		id,
		agent_id: agentId,
		agent_name: agentName,
		asked,
		did,
		answered: "Work completed successfully.",
		input: { ticket_id: 428950, request: asked },
		output: { ticket_id: 428950, completed: true },
		steps,
		child_run_ids: childRunIds,
		child_runs: childRuns,
	});

	const runs = new Map([
		[
			parentId,
			detail(
				parentId,
				"Service Desk Triage",
				"Triage ticket 428950",
				"Looked up the ticket with [ai_ticketing_get_ticket_details], then asked [delegate_to_troubleshooting_agent] to collect evidence.",
				[
					step(parentId, 1, "llm_request", {
						messages_count: 2,
						tools_count: 17,
					}),
					step(parentId, 2, "llm_response", {
						content: "",
						tool_calls: [
							{ name: "ai_ticketing_get_ticket_details" },
						],
					}),
					step(parentId, 3, "tool_call", {
						tool_name: "ai_ticketing_get_ticket_details",
						arguments: { ticket_id: 428950 },
					}),
					step(parentId, 4, "tool_result", {
						tool_name: "ai_ticketing_get_ticket_details",
						execution_id: "92000000-0000-4000-8000-000000000001",
						result: {
							ticket_id: 428950,
							status: "open",
							matched: true,
						},
					}),
					step(parentId, 5, "tool_call", {
						tool_name: "delegate_to_troubleshooting_agent",
						arguments: { task: "Collect endpoint evidence" },
					}),
					step(parentId, 6, "tool_result", {
						tool_name: "delegate_to_troubleshooting_agent",
						result: { status: "complete" },
						child_run_id: childId,
					}),
				],
				[childId],
				[
					{
						id: childId,
						agent_id: agentId,
						agent_name: "Troubleshooting Specialist",
						status: "completed",
						asked: "Collect endpoint evidence",
						did: "Resolved the device and checked disk space and alerts.",
						answered: "Work completed successfully.",
						duration_ms: 18_250,
						created_at: createdAt,
					},
				],
			),
		],
		[
			childId,
			detail(
				childId,
				"Troubleshooting Specialist",
				"Collect endpoint evidence",
				"Resolved the device and checked disk space and alerts.",
				[
					step(childId, 1, "tool_call", {
						tool_name: "ninja_get_device_details",
						arguments: { ticket_id: 428950 },
					}),
					step(childId, 2, "tool_result", {
						tool_name: "ninja_get_device_details",
						result: { device: "ELIJAH-LT", matched: true },
					}),
				],
				// This intentionally has no matching delegation step. It covers
				// historical children reconstructed from parent_run_id alone.
				[grandchildId],
				[
					{
						id: grandchildId,
						agent_id: agentId,
						agent_name: "Asset Resolver",
						status: "completed",
						asked: "Confirm the managed asset",
						did: "Matched the requester to ELIJAH-LT.",
						answered: "Work completed successfully.",
						duration_ms: 18_250,
						created_at: createdAt,
					},
				],
			),
		],
		[
			grandchildId,
			detail(
				grandchildId,
				"Asset Resolver",
				"Confirm the managed asset",
				"Matched the requester to ELIJAH-LT.",
				[
					step(grandchildId, 1, "tool_result", {
						tool_name: "rmm_resolve_asset",
						result: { device: "ELIJAH-LT", matched: true },
					}),
				],
				[],
			),
		],
	]);

	await page.route("**/api/agent-runs*", async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		if (
			request.method() === "GET" &&
			url.pathname === "/api/agent-runs" &&
			url.searchParams.get("agent_id") === agentId
		) {
			await route.fulfill({
				status: 200,
				contentType: "application/json",
				body: JSON.stringify({
					items: [runs.get(parentId)],
					total: 1,
					next_cursor: null,
				}),
			});
			return;
		}
		await route.fallback();
	});

	await page.route(/\/api\/agent-runs\/[^/?]+(?:\?.*)?$/, async (route) => {
		if (route.request().method() !== "GET") {
			await route.fallback();
			return;
		}
		const runId = new URL(route.request().url()).pathname.split("/").at(-1);
		const run = runId ? runs.get(runId) : undefined;
		if (!run) {
			await route.fallback();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify(run),
		});
	});

	return { parentId, childId, grandchildId };
}

test.describe("Agent Detail — Runs Tab (admin)", () => {
	test("groups run activity, expands delegated traces, and reserves raw data for Advanced", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Activity Story ${Date.now()}`,
				description: "Hierarchical run activity coverage",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();
		const { parentId, grandchildId } = await mockHierarchicalRun(
			page,
			agent.id,
		);

		try {
			await page.setViewportSize({ width: 1440, height: 1000 });
			await page.goto(`/agents/${agent.id}/runs/${parentId}`);
			const activity = page.locator('[data-slot="run-activity"]');
			await expect(activity).toBeVisible();
			await expect(
				activity.getByText("Looked up ticket details", { exact: true }),
			).toBeVisible();
			await expect(
				activity.getByText(
					"Ticket: 428950 · Status: Open · Match found",
				),
			).toBeVisible();
			const ticketAction = activity
				.locator('[data-activity-kind="action"]')
				.filter({ hasText: "Looked up ticket details" });
			const ticketReference = page.getByRole("link", {
				name: "Show Looked up ticket details in Activity",
			});
			await expect(ticketReference).toBeVisible();
			await ticketReference.hover();
			await expect(ticketAction).toHaveAttribute(
				"data-highlighted",
				"true",
			);
			await page.getByText("What was asked", { exact: true }).hover();
			await expect(ticketAction).toHaveAttribute(
				"data-highlighted",
				"false",
			);
			await ticketReference.click();
			await expect(ticketAction).toBeInViewport();
			await expect(ticketAction).toBeFocused();
			await page.mouse.move(1, 1);
			await expect(ticketAction).toHaveAttribute(
				"data-highlighted",
				"false",
			);
			await expect(
				ticketAction.getByRole("link", { name: "Execution" }),
			).toHaveAttribute(
				"href",
				"/history/92000000-0000-4000-8000-000000000001",
			);
			await expect(
				page.getByText("Troubleshooting Specialist", { exact: true }),
			).toHaveCount(2);
			await expect(
				activity.getByRole("link", {
					name: "Open Troubleshooting Specialist run",
				}),
			).toHaveAttribute(
				"href",
				`/agents/${agent.id}/runs/91000000-0000-4000-8000-000000000002`,
			);
			const delegatedActivity = activity
				.locator("[data-activity-kind='delegation']")
				.filter({ hasText: "Troubleshooting Specialist" })
				.first();
			const delegatedTitle = delegatedActivity.getByText(
				"Troubleshooting Specialist",
				{ exact: true },
			);
			const delegatedStatus = delegatedActivity.getByLabel(
				"Delegated run status: Completed",
			);
			await expect(delegatedStatus).toBeVisible();
			const delegatedTitleBox = await delegatedTitle.boundingBox();
			const delegatedStatusBox = await delegatedStatus.boundingBox();
			expect(
				Math.abs(
					(delegatedTitleBox?.y ?? 0) +
						(delegatedTitleBox?.height ?? 0) / 2 -
						((delegatedStatusBox?.y ?? 0) +
							(delegatedStatusBox?.height ?? 0) / 2),
				),
			).toBeLessThanOrEqual(4);
			await expect(
				page.getByText("Raw input", { exact: true }),
			).toHaveCount(0);
			await expect(
				page.getByText("Called ai_ticketing_get_ticket_details", {
					exact: true,
				}),
			).not.toBeVisible();
			await expect(page.getByText("Run ID", { exact: true })).toHaveCount(
				0,
			);
			await expect(
				page.getByText("gpt-5.2", { exact: true }),
			).toHaveCount(0);

			await delegatedTitle.click();
			await expect(
				delegatedActivity.getByRole("button", {
					name: /hide details for troubleshooting specialist/i,
				}),
			).toHaveAttribute("aria-expanded", "true");
			await expect(
				page.getByText(
					"Resolved the device and checked disk space and alerts.",
				),
			).toBeVisible();
			const nestedDelegation = delegatedActivity
				.locator("[data-activity-kind='delegation']")
				.filter({ hasText: "Asset Resolver" });
			await nestedDelegation
				.getByText("Confirm the managed asset", { exact: true })
				.click();
			await expect(
				page.getByText("Asset Resolver", {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				page.getByText("Matched the requester to ELIJAH-LT."),
			).toBeVisible();
			await expect(
				nestedDelegation.getByRole("button", {
					name: /hide details for asset resolver/i,
				}),
			).toHaveAttribute("aria-expanded", "true");

			await nestedDelegation
				.getByRole("link", { name: "Open Asset Resolver run" })
				.click();
			await expect(page).toHaveURL(
				new RegExp(`/agents/${agent.id}/runs/${grandchildId}$`),
			);
			const contextualBack = page.getByTestId("run-context-back");
			await expect(contextualBack).toHaveText(
				"Back to Service Desk Triage run",
			);
			await expect(contextualBack).toHaveAttribute(
				"href",
				`/agents/${agent.id}/runs/${parentId}`,
			);
			await contextualBack.click();
			await expect(page).toHaveURL(
				new RegExp(`/agents/${agent.id}/runs/${parentId}$`),
			);
			await expect(
				delegatedActivity.getByRole("button", {
					name: /hide details for troubleshooting specialist/i,
				}),
			).toHaveAttribute("aria-expanded", "true");
			await expect(
				nestedDelegation.getByRole("button", {
					name: /hide details for asset resolver/i,
				}),
			).toHaveAttribute("aria-expanded", "true");
			await expect(nestedDelegation).toBeInViewport();

			await page
				.getByRole("button", { name: "Advanced", exact: true })
				.click();
			await expect(
				page.getByText("Called ai_ticketing_get_ticket_details", {
					exact: true,
				}),
			).not.toBeVisible();
			await ticketAction.getByText("Details", { exact: true }).click();
			await expect(
				ticketAction.getByText("ai_ticketing_get_ticket_details", {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				page.getByText("Raw input", { exact: true }),
			).toBeVisible();
			await expect(page.getByText(/\{"ticket_id"/)).toHaveCount(0);
			await expect(
				ticketAction.getByText("ticket_id:", { exact: true }),
			).toHaveCount(2);
			await page.getByText("Raw executor trace", { exact: true }).click();
			await expect(
				page.getByText("Called ai_ticketing_get_ticket_details", {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				page.getByText("Run ID", { exact: true }),
			).toBeVisible();
			await expect(
				page.getByText("gpt-5.2", { exact: true }),
			).toBeVisible();

			await page.setViewportSize({ width: 390, height: 844 });
			await page.reload();
			await expect(activity).toBeVisible();
			expect(
				await activity.evaluate(
					(element) => element.scrollWidth - element.clientWidth,
				),
			).toBeLessThanOrEqual(1);
			const mobileOpenRun = activity.getByRole("link", {
				name: "Open Troubleshooting Specialist run",
			});
			const mobileShowDetails = activity.getByRole("button", {
				name: "Show details for Troubleshooting Specialist",
			});
			await expect(mobileOpenRun).toBeVisible();
			await expect(mobileShowDetails).toBeVisible();
			const mobileOpenRunBox = await mobileOpenRun.boundingBox();
			const mobileShowDetailsBox = await mobileShowDetails.boundingBox();
			expect(mobileOpenRunBox?.height).toBeGreaterThanOrEqual(44);
			expect(mobileOpenRunBox?.height).toBeLessThanOrEqual(60);
			expect(mobileShowDetailsBox?.height).toBeGreaterThanOrEqual(44);
			expect(mobileShowDetailsBox?.width).toBeGreaterThan(
				mobileOpenRunBox?.width ?? 0,
			);
			await mobileShowDetails
				.getByText("Collect endpoint evidence", { exact: true })
				.click();
			await expect(
				activity.getByRole("button", {
					name: "Hide details for Troubleshooting Specialist",
				}),
			).toHaveAttribute("aria-expanded", "true");
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("uses the same human activity view in the Runs drawer", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Runs Drawer Activity ${Date.now()}`,
				description: "Runs drawer activity coverage",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();
		await mockHierarchicalRun(page, agent.id);

		try {
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`/agents/${agent.id}?tab=runs`);
			const runHistory = page.getByRole("region", {
				name: "Run history",
			});
			await expect(runHistory).toBeVisible();
			await runHistory
				.locator('[data-slot="run-card"] > [role="button"]')
				.first()
				.click();

			const sheet = page.getByRole("dialog");
			await expect(sheet).toBeVisible();
			await expect(sheet).toHaveAttribute("aria-label", "Run review");
			const activity = sheet.locator('[data-slot="run-activity"]');
			await expect(
				activity.getByRole("heading", { name: "Activity" }),
			).toBeVisible();
			await expect(
				activity.getByText("Looked up ticket details", {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				activity.getByText("Troubleshooting Specialist", {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				sheet.getByText("Raw executor trace", { exact: true }),
			).toHaveCount(0);

			await activity
				.getByText("Collect endpoint evidence", { exact: true })
				.click();
			await expect(
				activity.getByText("Looked up device details", {
					exact: true,
				}),
			).toBeVisible();
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("shows agent detail with tabs and Runs view", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Detail Test ${Date.now()}`,
				description: "e2e",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();

		try {
			await page.goto(`/agents/${agent.id}`);

			// Page header visible
			await expect(
				page.getByRole("heading", { name: agent.name }).first(),
			).toBeVisible({ timeout: 10000 });

			// Tabs visible — Overview, Runs, Settings
			await expect(
				page.getByRole("tab", { name: /overview/i }),
			).toBeVisible();
			await expect(
				page.getByRole("tab", { name: /runs/i }),
			).toBeVisible();
			await expect(
				page.getByRole("tab", { name: /settings/i }),
			).toBeVisible();

			// Click Runs tab
			await page.getByRole("tab", { name: /runs/i }).click();

			// Either run cards or empty state — accept either; this agent
			// has zero runs so we expect the empty state.
			await expect(
				page
					.getByText(/no runs|nothing yet|no flagged runs/i)
					.or(page.getByRole("table"))
					.first(),
			).toBeVisible({ timeout: 5000 });

			await page.screenshot({
				path: "test-results/screenshots/agent-detail-runs.png",
				fullPage: true,
			});
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("Settings tab is the only active tab in create mode", async ({
		page,
	}) => {
		await page.goto("/agents/new");
		await expect(page.getByRole("tab", { name: /settings/i })).toBeVisible({
			timeout: 10000,
		});
		// Overview/Runs disabled in create mode.
		const overviewTab = page.getByRole("tab", { name: /overview/i });
		await expect(overviewTab).toBeDisabled();
	});

	test("does not reserve an empty scrollbar lane for sparse recent activity", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Sparse Overview ${Date.now()}`,
				description: "Sparse recent activity gutter coverage",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();
		await mockRunHistory(page, agent.id, 1);

		try {
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`/agents/${agent.id}`);
			const recentRegion = page.getByRole("region", {
				name: "Recent activity",
			});
			const recentRun = recentRegion.getByRole("link").filter({
				hasText: "Mock run 1",
			});
			await expect(recentRun).toBeVisible();
			expect(
				await recentRegion.evaluate(
					(element) => getComputedStyle(element).scrollbarGutter,
				),
			).toBe("auto");

			const [regionBox, runBox] = await Promise.all([
				recentRegion.boundingBox(),
				recentRun.boundingBox(),
			]);
			if (!regionBox || !runBox) {
				throw new Error("Expected sparse recent activity geometry");
			}
			expect(
				Math.abs(
					regionBox.x + regionBox.width - (runBox.x + runBox.width),
				),
			).toBeLessThanOrEqual(1);
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("keeps Overview context fixed and View all runs navigates", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Overview Workspace ${Date.now()}`,
				description: "Overview viewport ownership regression coverage",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();
		await mockRunHistory(page, agent.id);

		try {
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`/agents/${agent.id}`);

			const main = page.locator("main");
			const heading = page.getByRole("heading", { name: agent.name });
			const tabs = page.getByRole("tablist");
			const activityHeading = page.getByText(/Activity — last 7 days/i);
			const recentRegion = page.getByRole("region", {
				name: "Recent activity",
			});
			await expect(recentRegion).toBeVisible();
			await expect(
				recentRegion.getByText("Delegated", { exact: true }).first(),
			).toBeVisible();

			expect(
				await main.evaluate(
					(element) => element.scrollHeight - element.clientHeight,
				),
			).toBeLessThanOrEqual(1);
			await main.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			expect(await main.evaluate((element) => element.scrollTop)).toBe(0);

			const desktopMetrics = await recentRegion.evaluate((element) => ({
				overflowY: getComputedStyle(element).overflowY,
				clientHeight: element.clientHeight,
				scrollHeight: element.scrollHeight,
			}));
			expect(desktopMetrics.overflowY).toBe("auto");
			expect(desktopMetrics.scrollHeight).toBeGreaterThan(
				desktopMetrics.clientHeight,
			);

			const contextBefore = await Promise.all([
				heading.boundingBox(),
				tabs.boundingBox(),
				activityHeading.boundingBox(),
			]);
			if (contextBefore.some((bounds) => bounds === null)) {
				throw new Error("Expected Overview context to be visible");
			}

			await recentRegion.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			await expect
				.poll(() =>
					recentRegion.evaluate((element) => element.scrollTop),
				)
				.toBeGreaterThan(0);
			expect(await main.evaluate((element) => element.scrollTop)).toBe(0);

			const contextAfter = await Promise.all([
				heading.boundingBox(),
				tabs.boundingBox(),
				activityHeading.boundingBox(),
			]);
			for (let index = 0; index < contextBefore.length; index += 1) {
				expect(
					Math.abs(
						(contextAfter[index]?.y ?? 0) -
							(contextBefore[index]?.y ?? 0),
					),
				).toBeLessThanOrEqual(1);
			}

			await page.getByRole("link", { name: /view all runs/i }).click();
			await expect(page).toHaveURL(
				new RegExp(`/agents/${agent.id}\\?tab=runs$`),
			);
			await expect(
				page.getByRole("tab", { name: /runs/i }),
			).toHaveAttribute("aria-selected", "true");

			for (const viewport of [
				{ width: 390, height: 844 },
				{ width: 1440, height: 650 },
			]) {
				await page.setViewportSize(viewport);
				await page.goto(`/agents/${agent.id}`);
				await expect(recentRegion).toBeVisible();

				const fallbackMetrics = await recentRegion.evaluate(
					(element) => ({
						overflowY: getComputedStyle(element).overflowY,
						clientHeight: element.clientHeight,
						scrollHeight: element.scrollHeight,
					}),
				);
				expect(fallbackMetrics.overflowY).not.toBe("auto");
				expect(
					Math.abs(
						fallbackMetrics.scrollHeight -
							fallbackMetrics.clientHeight,
					),
				).toBeLessThanOrEqual(1);
				expect(
					await main.evaluate(
						(element) =>
							element.scrollHeight - element.clientHeight,
					),
				).toBeGreaterThan(0);
				expect(
					await main.evaluate(
						(element) => element.scrollWidth - element.clientWidth,
					),
				).toBeLessThanOrEqual(1);
			}
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("keeps run context fixed on desktop and falls back to page scrolling", async ({
		page,
		api,
	}) => {
		const create = await api.post("/api/agents", {
			data: {
				name: `E2E Run Workspace ${Date.now()}`,
				description: "Viewport ownership regression coverage",
				system_prompt: "test",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(create.ok()).toBeTruthy();
		const agent = await create.json();
		await mockRunHistory(page, agent.id);

		try {
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`/agents/${agent.id}?tab=runs`);

			const main = page.locator("main");
			const heading = page.getByRole("heading", { name: agent.name });
			const tabs = page.getByRole("tablist");
			const search = page.getByLabel("Search runs");
			const runRegion = page.getByRole("region", {
				name: "Run history",
			});
			await expect(runRegion).toBeVisible();
			await expect(
				runRegion.getByText("Delegated", { exact: true }).first(),
			).toBeVisible();
			const mainOverflow = await main.evaluate(
				(element) => element.scrollHeight - element.clientHeight,
			);
			expect(mainOverflow).toBeLessThanOrEqual(1);
			await main.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			expect(await main.evaluate((element) => element.scrollTop)).toBe(0);

			const desktopMetrics = await runRegion.evaluate((element) => ({
				overflowY: getComputedStyle(element).overflowY,
				clientHeight: element.clientHeight,
				scrollHeight: element.scrollHeight,
			}));
			expect(desktopMetrics.overflowY).toBe("auto");
			expect(desktopMetrics.scrollHeight).toBeGreaterThan(
				desktopMetrics.clientHeight,
			);
			const firstRunCard = runRegion
				.locator('[data-slot="run-card"]')
				.first();
			const [tabsBox, runCardBox] = await Promise.all([
				tabs.boundingBox(),
				firstRunCard.boundingBox(),
			]);
			if (!tabsBox || !runCardBox) {
				throw new Error("Expected tab and run card geometry");
			}
			expect(Math.abs(tabsBox.x - runCardBox.x)).toBeLessThanOrEqual(1);
			expect(
				Math.abs(
					tabsBox.x +
						tabsBox.width -
						(runCardBox.x + runCardBox.width),
				),
			).toBeLessThanOrEqual(1);
			const runRegionSpacing = await runRegion.evaluate((element) => {
				const style = getComputedStyle(element);
				return {
					scrollbarGutter: style.scrollbarGutter,
					paddingRight: style.paddingRight,
				};
			});
			expect(runRegionSpacing).toEqual({
				scrollbarGutter: "auto",
				paddingRight: "0px",
			});

			const contextBefore = await Promise.all([
				heading.boundingBox(),
				tabs.boundingBox(),
				search.boundingBox(),
			]);
			if (contextBefore.some((bounds) => bounds === null)) {
				throw new Error(
					"Expected agent context controls to be visible",
				);
			}

			await runRegion.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			await expect
				.poll(() => runRegion.evaluate((element) => element.scrollTop))
				.toBeGreaterThan(0);
			expect(await main.evaluate((element) => element.scrollTop)).toBe(0);
			await expect(
				page.getByText(/Mock run 24:/).first(),
			).toBeInViewport();

			const contextAfter = await Promise.all([
				heading.boundingBox(),
				tabs.boundingBox(),
				search.boundingBox(),
			]);
			for (let index = 0; index < contextBefore.length; index += 1) {
				expect(contextAfter[index]?.y).toBeCloseTo(
					contextBefore[index]?.y ?? 0,
					1,
				);
			}

			await page.setViewportSize({ width: 1440, height: 720 });
			const addCapturedDataFilter = page.getByRole("button", {
				name: "Add captured data filter",
			});
			for (let index = 0; index < 6; index += 1) {
				await addCapturedDataFilter.click();
			}
			const filterRegion = page.locator(".agent-runs-filter-region");
			const filterMetrics = await filterRegion.evaluate((element) => ({
				overflowY: getComputedStyle(element).overflowY,
				clientHeight: element.clientHeight,
				scrollHeight: element.scrollHeight,
			}));
			expect(filterMetrics.overflowY).toBe("auto");
			expect(filterMetrics.scrollHeight).toBeGreaterThan(
				filterMetrics.clientHeight,
			);
			expect(
				await runRegion.evaluate((element) => element.clientHeight),
			).toBeGreaterThan(0);
			expect(
				await main.evaluate(
					(element) => element.scrollHeight - element.clientHeight,
				),
			).toBeLessThanOrEqual(1);
			await filterRegion.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			await expect
				.poll(() =>
					filterRegion.evaluate((element) => element.scrollTop),
				)
				.toBeGreaterThan(0);

			for (const viewport of [
				{ width: 390, height: 844 },
				{ width: 1440, height: 650 },
			]) {
				await page.setViewportSize(viewport);
				await page.reload();
				await expect(runRegion).toBeVisible();

				const fallbackMetrics = await runRegion.evaluate((element) => ({
					overflowY: getComputedStyle(element).overflowY,
					clientHeight: element.clientHeight,
					scrollHeight: element.scrollHeight,
				}));
				expect(fallbackMetrics.overflowY).not.toBe("auto");
				expect(
					Math.abs(
						fallbackMetrics.scrollHeight -
							fallbackMetrics.clientHeight,
					),
				).toBeLessThanOrEqual(1);
				expect(
					await main.evaluate(
						(element) =>
							element.scrollHeight - element.clientHeight,
					),
				).toBeGreaterThan(0);

				await runRegion.evaluate((element) => {
					element.scrollTop = 100;
				});
				expect(
					await runRegion.evaluate((element) => element.scrollTop),
				).toBe(0);

				await main.evaluate((element) => {
					element.scrollTop = element.scrollHeight;
				});
				await expect
					.poll(() => main.evaluate((element) => element.scrollTop))
					.toBeGreaterThan(0);

				const horizontalOverflow = await main.evaluate(
					(element) => element.scrollWidth - element.clientWidth,
				);
				expect(horizontalOverflow).toBeLessThanOrEqual(1);
			}
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});
});
