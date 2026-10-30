/**
 * Agent tuning apply acceptance (Admin)
 *
 * The API has focused coverage for verdict clearing and prompt history when
 * real flagged runs exist. This browser spec keeps flagged-run/proposal setup
 * local so it can exercise Apply deterministically without invoking the tuning
 * model; real flagged-run creation is covered by the chat-run plus verdict API
 * pattern in agents-review-verdict.admin.spec.ts.
 */

import { expect, test, type AuthedApi } from "./fixtures/api-fixture";
import type { Page } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const ORIGINAL_PROMPT = `Original tuning prompt ${UNIQUE}`;
const PROPOSED_PROMPT = `Proposed tuning prompt ${UNIQUE}`;
const FINAL_PROMPT = `Final applied tuning prompt ${UNIQUE}`;

type Agent = { id: string; name: string; system_prompt: string };

type AgentRun = {
	id: string;
	agent_id: string;
	agent_name: string;
	trigger_type: string;
	trigger_source: string | null;
	conversation_id: string | null;
	event_delivery_id: string | null;
	input: Record<string, unknown>;
	output: Record<string, unknown>;
	status: string;
	error: string | null;
	org_id: string | null;
	caller_user_id: string | null;
	caller_email: string | null;
	caller_name: string;
	iterations_used: number;
	tokens_used: number;
	budget_max_iterations: number;
	budget_max_tokens: number;
	duration_ms: number;
	llm_model: string;
	asked: string;
	did: string;
	answered: string;
	metadata: Record<string, string>;
	confidence: number;
	confidence_reason: string;
	summary_status: string;
	summary_error: string | null;
	verdict: "down";
	verdict_note: string;
	verdict_set_at: string;
	verdict_set_by: string | null;
	created_at: string;
	started_at: string;
	completed_at: string;
	parent_run_id: string | null;
};

async function expectOk(response: { ok(): boolean; text(): Promise<string> }) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function createAgent(api: AuthedApi): Promise<Agent> {
	const response = await api.post("/api/agents", {
		data: {
			name: `E2E Tuning Apply ${UNIQUE}`,
			description: "Tuning apply acceptance fixture",
			system_prompt: ORIGINAL_PROMPT,
			access_level: "authenticated",
		},
	});
	await expectOk(response);
	return (await response.json()) as Agent;
}

function flaggedRun(agent: Agent): AgentRun {
	const createdAt = new Date().toISOString();
	return {
		id: "93000000-0000-4000-8000-000000000001",
		agent_id: agent.id,
		agent_name: agent.name,
		trigger_type: "manual",
		trigger_source: "Playwright",
		conversation_id: null,
		event_delivery_id: null,
		input: { request: `ambiguous request ${UNIQUE}` },
		output: { answer: "Routed without asking a clarifying question." },
		status: "completed",
		error: null,
		org_id: null,
		caller_user_id: null,
		caller_email: null,
		caller_name: "E2E Operator",
		iterations_used: 1,
		tokens_used: 100,
		budget_max_iterations: 50,
		budget_max_tokens: 100_000,
		duration_ms: 1000,
		llm_model: "local-fixture",
		asked: `Ambiguous request ${UNIQUE}`,
		did: "Answered without clarification.",
		answered: "Routed without asking a clarifying question.",
		metadata: { fixture: UNIQUE },
		confidence: 0.5,
		confidence_reason: "Synthetic local fixture",
		summary_status: "completed",
		summary_error: null,
		verdict: "down",
		verdict_note: "Should ask a clarifying question first.",
		verdict_set_at: createdAt,
		verdict_set_by: null,
		created_at: createdAt,
		started_at: createdAt,
		completed_at: createdAt,
		parent_run_id: null,
	};
}

async function routeDeterministicTuningSetup(page: Page, agent: Agent) {
	const run = flaggedRun(agent);
	await page.route("**/api/agent-runs?**", async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		if (
			request.method() !== "GET" ||
			url.pathname !== "/api/agent-runs" ||
			url.searchParams.get("agent_id") !== agent.id ||
			url.searchParams.get("verdict") !== "down"
		) {
			await route.fallback();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({ items: [run], total: 1, next_cursor: null }),
		});
	});

	await page.route(
		`**/api/agents/${agent.id}/tuning-session`,
		async (route) => {
			if (route.request().method() !== "POST") {
				await route.fallback();
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: "application/json",
				body: JSON.stringify({
					summary: "Ask before routing ambiguous requests.",
					proposed_prompt: PROPOSED_PROMPT,
					affected_run_ids: [run.id],
				}),
			});
		},
	);
}

test.describe("agent tuning apply acceptance", () => {
	test("applies an edited proposal through the real API and persists the live prompt", async ({
		page,
		api,
	}) => {
		const agent = await createAgent(api);
		try {
			await routeDeterministicTuningSetup(page, agent);

			await page.goto(`/agents/${agent.id}/tune`);
			await expect(
				page.getByRole("heading", { name: /tune agent/i }),
			).toBeVisible({ timeout: 10_000 });
			await page
				.getByRole("button", { name: /generate proposal from 1 run/i })
				.click();

			const promptEditor = page.getByLabel("Proposed prompt (editable)");
			await expect(promptEditor).toHaveValue(PROPOSED_PROMPT);
			await promptEditor.fill(FINAL_PROMPT);

			const applied = page.waitForResponse(
				(response) =>
					response
						.url()
						.endsWith(
							`/api/agents/${agent.id}/tuning-session/apply`,
						) && response.request().method() === "POST",
			);
			await page.getByRole("button", { name: "Apply live" }).click();
			const applyResponse = await applied;
			await expectOk(applyResponse);
			const applyBody = (await applyResponse.json()) as {
				agent_id: string;
				history_id: string;
			};
			expect(applyBody.agent_id).toBe(agent.id);
			expect(applyBody.history_id).toMatch(/^[0-9a-f-]{36}$/i);

			await expect(page).toHaveURL(new RegExp(`/agents/${agent.id}$`));
			const persisted = await api.get(`/api/agents/${agent.id}`);
			await expectOk(persisted);
			expect(((await persisted.json()) as Agent).system_prompt).toBe(
				FINAL_PROMPT,
			);
		} finally {
			const deleted = await api.delete(`/api/agents/${agent.id}`);
			expect([200, 204, 404]).toContain(deleted.status());
		}
	});
});
