import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const AGENT_NAME = `Private member agent ${UNIQUE}`;
const AGENT_DESCRIPTION = `Member-owned acceptance agent ${UNIQUE}`;
const AGENT_PROMPT =
	"You are a private acceptance agent. Keep answers concise and only use chat.";

const ADMIN_ONLY_FIELDS = [
	"organization_id",
	"system_tools",
	"knowledge_sources",
	"delegated_agent_ids",
	"role_ids",
	"mcp_connection_ids",
];

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

test(
	"[AGENT-01 desktop] member creates private agent and reopens persisted settings",
	{ tag: "@smoke" },
	async ({ page, api }) => {
		let agentId: string | null = null;

		try {
			await page.goto("/agents/new");
			await expect(
				page.getByRole("heading", { name: "New agent" }),
			).toBeVisible({ timeout: 10000 });
			await expect(
				page.getByRole("tab", { name: "Settings" }),
			).toHaveAttribute("aria-selected", "true");
			await expect(page.getByLabel("Scope")).toHaveText(/Only me/);
			await expect(
				page.getByText(
					"Private agents are available only to their owner.",
				),
			).toBeVisible();
			await expect(page.getByLabel("Access level")).toHaveCount(0);

			await page.getByLabel("Name", { exact: true }).fill(AGENT_NAME);
			await page.getByLabel("Description").fill(AGENT_DESCRIPTION);
			await page
				.getByRole("textbox", { name: "System prompt" })
				.fill(AGENT_PROMPT);
			await expect(
				page.getByRole("combobox", { name: "Channels" }),
			).toHaveText(/1 selected|Web Chat|chat/i);

			const createRequest = page.waitForRequest(
				(request) =>
					request.url().includes("/api/agents") &&
					request.method() === "POST",
			);
			const createResponse = page.waitForResponse(
				(response) =>
					response.url().includes("/api/agents") &&
					response.request().method() === "POST",
			);
			await page
				.getByRole("button", { name: "Create agent", exact: true })
				.click();

			const requestBody = (await createRequest).postDataJSON() as Record<
				string,
				unknown
			>;
			expect(requestBody).toMatchObject({
				name: AGENT_NAME,
				description: AGENT_DESCRIPTION,
				system_prompt: AGENT_PROMPT,
				channels: ["chat"],
				access_level: "private",
			});
			// Non-admins may not set these; the API refuses them instead of
			// silently dropping them, so the editor must not send them.
			for (const field of ADMIN_ONLY_FIELDS) {
				expect(requestBody).not.toHaveProperty(field);
			}

			const response = await createResponse;
			await expectOk(response);
			const created = (await response.json()) as {
				id: string;
				name: string;
				description: string;
				system_prompt: string;
				channels: string[];
				access_level: string;
				role_ids: string[];
				owner_user_id?: string | null;
			};
			agentId = created.id;
			expect(created).toMatchObject({
				name: AGENT_NAME,
				description: AGENT_DESCRIPTION,
				system_prompt: AGENT_PROMPT,
				channels: ["chat"],
				access_level: "private",
				role_ids: [],
			});
			expect(created.owner_user_id).toBeTruthy();

			await expect(page).toHaveURL(new RegExp(`/agents/${agentId}$`), {
				timeout: 10000,
			});
			await expect(
				page.getByRole("heading", { name: AGENT_NAME }),
			).toBeVisible();
			await expect(page.getByText(AGENT_DESCRIPTION)).toBeVisible();
			await expect(
				page.getByRole("button", { name: "Start chat" }),
			).toBeVisible();

			await page.reload();
			await expect(
				page.getByRole("heading", { name: AGENT_NAME }),
			).toBeVisible({
				timeout: 10000,
			});
			await page.getByRole("tab", { name: "Settings" }).click();
			await expect(page.getByLabel("Scope")).toHaveText(/Only me/);
			await expect(page.getByLabel("Name", { exact: true })).toHaveValue(
				AGENT_NAME,
			);
			await expect(page.getByLabel("Description")).toHaveValue(
				AGENT_DESCRIPTION,
			);
			await expect(
				page.getByRole("textbox", { name: "System prompt" }),
			).toContainText(AGENT_PROMPT);
			await expect(
				page.getByRole("combobox", { name: "Channels" }),
			).toHaveText(/1 selected|Web Chat|chat/i);
			await expect(page.getByLabel("Access level")).toHaveCount(0);
			await expect(
				page.getByText(/Could not load available roles/i),
			).toHaveCount(0);

			const UPDATED_DESCRIPTION = `${AGENT_DESCRIPTION} (edited)`;
			await page.getByLabel("Description").fill(UPDATED_DESCRIPTION);
			const updateRequest = page.waitForRequest(
				(request) =>
					request.url().includes(`/api/agents/${agentId}`) &&
					request.method() === "PUT",
			);
			const updateResponse = page.waitForResponse(
				(response) =>
					response.url().includes(`/api/agents/${agentId}`) &&
					response.request().method() === "PUT",
			);
			await page
				.getByRole("button", { name: "Save changes", exact: true })
				.click();
			const updateBody = (await updateRequest).postDataJSON() as Record<
				string,
				unknown
			>;
			for (const field of ADMIN_ONLY_FIELDS) {
				expect(updateBody).not.toHaveProperty(field);
			}
			await expectOk(await updateResponse);

			const fetched = await api.get(`/api/agents/${agentId}`);
			await expectOk(fetched);
			const persisted = (await fetched.json()) as {
				name: string;
				description: string;
				system_prompt: string;
				channels: string[];
				access_level: string;
				role_ids: string[];
				owner_user_id?: string | null;
			};
			expect(persisted).toMatchObject({
				name: AGENT_NAME,
				description: UPDATED_DESCRIPTION,
				system_prompt: AGENT_PROMPT,
				channels: ["chat"],
				access_level: "private",
				role_ids: [],
			});
			expect(persisted.owner_user_id).toBeTruthy();
		} finally {
			if (agentId) {
				await expectDeleted(await api.delete(`/api/agents/${agentId}`));
			}
		}
	},
);
