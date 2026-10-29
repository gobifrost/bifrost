import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const CONNECTION_NAME = `Chat Persistence Fixture ${UNIQUE}`;
const PROFILE_NAME = `Chat Persistence Profile ${UNIQUE}`;
const AGENT_NAME = `Chat Persistence Agent ${UNIQUE}`;
const USER_PROMPT = `Persist this fixture chat turn ${UNIQUE}`;

type CreatedRecord = { id: string };

interface ChatContextUsage {
	input_tokens: number;
	context_window: number | null;
	cache_read_tokens: number;
	model_profile_id: string;
}

interface ChatRunState {
	active_run: { id: string; status: string; error: string | null } | null;
	messages: Array<{ role: string; content: string | null }>;
	context_usage: ChatContextUsage | null;
}

async function expectOk(
	response: Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">,
) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
) {
	expect([200, 204, 404]).toContain(response.status());
}

async function createFixtureProfile(api: AuthedApi) {
	let connectionId: string | null = null;
	try {
		const connectionResponse = await api.post("/api/admin/ai/connections", {
			data: {
				name: CONNECTION_NAME,
				provider: "openai_compatible",
				api_key: "fixture-key",
				endpoint: "http://scheduler-fixtures:8080/v1",
			},
		});
		await expectOk(connectionResponse);
		const connection = (await connectionResponse.json()) as CreatedRecord;
		connectionId = connection.id;

		const profileResponse = await api.post("/api/admin/ai/profiles", {
			data: {
				name: PROFILE_NAME,
				connection_id: connection.id,
				model: "gpt-4o",
				capabilities: {
					tool_calling: false,
					image_input: false,
					pdf_input: false,
					source: "manual",
				},
				enabled_for_chat: true,
			},
		});
		await expectOk(profileResponse);
		const profile = (await profileResponse.json()) as CreatedRecord;

		return { connectionId: connection.id, profileId: profile.id };
	} catch (error) {
		if (connectionId) {
			await expectDeleted(
				await api.delete(`/api/admin/ai/connections/${connectionId}`),
			);
		}
		throw error;
	}
}

async function getChatState(api: AuthedApi, conversationId: string) {
	const response = await api.get(
		`/api/chat/conversations/${conversationId}/state`,
	);
	await expectOk(response);
	return (await response.json()) as ChatRunState;
}

async function waitForAssistantOrRunFailure(
	api: AuthedApi,
	page: import("@playwright/test").Page,
	conversationId: string,
	runId: string,
) {
	await expect
		.poll(async () => {
			const state = await getChatState(api, conversationId);
			const assistantMessage = state.messages.find(
				(message) =>
					message.role === "assistant" && message.content === "ok",
			);
			if (assistantMessage) return "assistant:ok";
			if (
				state.active_run?.id === runId &&
				state.active_run.status === "failed"
			) {
				throw new Error(
					state.active_run.error ?? "unknown chat run error",
				);
			}
			return state.active_run?.status ?? "pending";
		})
		.toBe("assistant:ok");
	await expect(page.getByRole("article", { name: "Assistant message" }).getByText("ok", { exact: true })).toBeVisible();
}

async function waitForContextUsage(
	api: AuthedApi,
	conversationId: string,
) {
	let contextUsage: ChatContextUsage | null = null;
	await expect
		.poll(async () => {
			contextUsage = (await getChatState(api, conversationId)).context_usage;
			return contextUsage !== null;
		})
		.toBe(true);
	if (!contextUsage) throw new Error("Chat context usage was not persisted");
	return contextUsage;
}

function formatTokenCount(tokens: number) {
	return tokens >= 1_000 ? `${Math.round(tokens / 1_000)}k` : String(tokens);
}

async function createFixtureAgent(api: AuthedApi, profileId: string) {
	const response = await api.post("/api/agents", {
		data: {
			name: AGENT_NAME,
			description: "Real persisted chat acceptance fixture.",
			system_prompt:
				"You are a deterministic acceptance-test assistant. Reply only with ok.",
			channels: ["chat"],
			access_level: "private",
			role_ids: [],
			system_tools: [],
			knowledge_sources: [],
			delegated_agent_ids: [],
			llm_profile_id: profileId,
		},
	});
	await expectOk(response);
	return (await response.json()) as CreatedRecord;
}

test("[CHAT-PERSIST-01 desktop] admin sends chat through local provider and reloads persisted messages", async ({
	page,
	api,
}) => {
	let conversationId: string | null = null;
	let agentId: string | null = null;
	let profileId: string | null = null;
	let connectionId: string | null = null;

	try {
		const fixtureProfile = await createFixtureProfile(api);
		profileId = fixtureProfile.profileId;
		connectionId = fixtureProfile.connectionId;

		const agent = await createFixtureAgent(api, profileId);
		agentId = agent.id;

		await page.goto(`/agents/${agentId}`);
		await expect(
			page.getByRole("heading", { name: AGENT_NAME }),
		).toBeVisible({ timeout: 10_000 });

		const conversationResponsePromise = page.waitForResponse(
			(response) =>
				response.request().method() === "POST" &&
				response.url().endsWith("/api/chat/conversations"),
		);
		await page.getByRole("button", { name: "Start chat" }).click();
		const conversationResponse = await conversationResponsePromise;
		await expectOk(conversationResponse);
		const conversation =
			(await conversationResponse.json()) as CreatedRecord & {
				agent_id: string | null;
			};
		conversationId = conversation.id;
		expect(conversation.agent_id).toBe(agentId);

		await expect(page).toHaveURL(new RegExp(`/chat/${conversationId}$`));
		await expect(page.getByText(`Chat with ${AGENT_NAME}`)).toBeVisible({
			timeout: 10_000,
		});
		const responseModel = page.getByRole("combobox", {
			name: "Response model",
		});
		await responseModel.click();
		await page.getByRole("option", { name: PROFILE_NAME, exact: true }).click();
		await expect(responseModel).toHaveText(PROFILE_NAME);

		const runResponsePromise = page.waitForResponse(
			(response) =>
				response.request().method() === "POST" &&
				response.url().endsWith("/api/chat/runs"),
		);
		await page
			.getByRole("textbox", { name: "Chat input" })
			.fill(USER_PROMPT);
		await page.getByRole("button", { name: "Send message" }).click();
		const runResponse = await runResponsePromise;
		await expectOk(runResponse);
		expect(runResponse.request().postDataJSON()).toMatchObject({
			model_profile_id: profileId,
		});
		const run = (await runResponse.json()) as { run_id: string };

		await expect(page.getByText(USER_PROMPT)).toBeVisible();
		await waitForAssistantOrRunFailure(
			api,
			page,
			conversationId,
			run.run_id,
		);
		const contextUsage = await waitForContextUsage(api, conversationId);
		expect(contextUsage).toMatchObject({
			input_tokens: 1,
			cache_read_tokens: 0,
			model_profile_id: profileId,
		});
		expect(contextUsage.context_window).toEqual(expect.any(Number));
		const contextWindow = contextUsage.context_window;
		if (!contextWindow) throw new Error("Fixture model has no context window");
		const usedPercent = Math.round(
			(contextUsage.input_tokens / contextWindow) * 100,
		);
		const cachedPercent = contextUsage.input_tokens
			? Math.round(
				(contextUsage.cache_read_tokens / contextUsage.input_tokens) * 100,
			)
			: 0;
		const expectedUsageSummary = `${usedPercent}% used (${100 - usedPercent}% left)`;
		const expectedTokenSummary = `${formatTokenCount(contextUsage.input_tokens)} / ${formatTokenCount(contextWindow)} tokens (${cachedPercent}% cached)`;
		await expect(page.getByText(/^In: \d/)).toHaveCount(0);
		await expect(page.getByText(/^Out: \d/)).toHaveCount(0);

		await page.getByRole("button", { name: "Context window" }).click();
		await expect(page.getByText(expectedUsageSummary, { exact: true })).toBeVisible();
		await expect(page.getByText(expectedTokenSummary, { exact: true })).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-context-window.png",
			fullPage: true,
			animations: "disabled",
		});

		await expect
			.poll(async () => {
				if (!conversationId) return [];
				const response = await api.get(
					`/api/chat/conversations/${conversationId}/state`,
				);
				await expectOk(response);
				const state = (await response.json()) as {
					messages: Array<{ role: string; content: string | null }>;
				};
				return state.messages.map((message) => ({
					role: message.role,
					content: message.content,
				}));
			})
			.toEqual([
				{ role: "user", content: USER_PROMPT },
				{ role: "assistant", content: "ok" },
			]);

		await page.reload();
		await expect(page).toHaveURL(new RegExp(`/chat/${conversationId}$`));
		await expect(page.getByText(USER_PROMPT)).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByRole("article", { name: "Assistant message" }).getByText("ok", { exact: true })).toBeVisible();
		await expect(responseModel).toHaveText(PROFILE_NAME);
		await page.getByRole("button", { name: "Context window" }).click();
		await expect(page.getByText(expectedUsageSummary, { exact: true })).toBeVisible();
		await expect(page.getByText(expectedTokenSummary, { exact: true })).toBeVisible();

		await page.keyboard.press("Escape");
		await page.getByRole("button", { name: "Toggle theme" }).click();
		await page.getByRole("button", { name: "Context window" }).click();
		await expect(page.getByText(expectedTokenSummary, { exact: true })).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-context-window-alternate-theme.png",
			fullPage: true,
			animations: "disabled",
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.getByRole("button", { name: "Context window" }).click();
		await expect(page.getByText(expectedTokenSummary, { exact: true })).toBeVisible();
		const popoutBounds = await page
			.getByRole("dialog", { name: "Context window" })
			.boundingBox();
		expect(popoutBounds).not.toBeNull();
		expect(popoutBounds!.x + popoutBounds!.width).toBeLessThanOrEqual(374);
		await page.screenshot({
			path: "playwright-results/screenshots/chat-context-window-mobile.png",
			fullPage: true,
			animations: "disabled",
		});
	} finally {
		if (conversationId) {
			await expectDeleted(
				await api.delete(`/api/chat/conversations/${conversationId}`),
			);
		}
		if (agentId) {
			await expectDeleted(await api.delete(`/api/agents/${agentId}`));
		}
		if (profileId) {
			await expectDeleted(
				await api.delete(`/api/admin/ai/profiles/${profileId}`),
			);
		}
		if (connectionId) {
			await expectDeleted(
				await api.delete(`/api/admin/ai/connections/${connectionId}`),
			);
		}
	}
});
