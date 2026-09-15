import { test, expect } from "./fixtures/api-fixture";
import type { APIResponse } from "@playwright/test";

const RUN_ID = Date.now();
const AGENT_NAME = `HOME-01 launch agent ${RUN_ID}`;

async function expectDeleted(response: APIResponse) {
	expect([200, 204, 404]).toContain(response.status());
}

test.describe("Home launch acceptance (admin)", () => {
	test("HOME-01 persists favorite state and opens a known agent resource", async ({
		page,
		api,
	}) => {
		const agentResponse = await api.post("/api/agents", {
			data: {
				name: AGENT_NAME,
				description: "Home launch acceptance resource",
				system_prompt: "You are a Home launch acceptance e2e resource.",
				access_level: "authenticated",
			},
		});
		expect(agentResponse.ok(), await agentResponse.text()).toBe(true);
		const agent = (await agentResponse.json()) as { id: string };
		let conversationId: string | undefined;

		try {
			await page.goto("/");
			await expect(
				page.getByRole("heading", {
					name: "Workspace",
					exact: true,
					level: 1,
				}),
			).toBeVisible({ timeout: 10000 });
			await expect(
				page.getByRole("region", { name: "Browse resources" }),
			).toBeVisible();
			await expect(
				page.getByRole("region", { name: "Pinned resources" }),
			).toHaveCount(0);
			await expect(
				page.getByRole("region", { name: "Explore resources" }),
			).toHaveCount(0);

			await page
				.getByRole("textbox", { name: "Search Home resources" })
				.fill(AGENT_NAME);
			await expect(
				page.getByRole("button", { name: AGENT_NAME, exact: true }),
			).toBeVisible();

			const pinnedSaved = page.waitForResponse(
				(response) =>
					response.url().includes("/api/home/preferences/") &&
					response.request().method() === "PUT",
			);
			await page
				.getByRole("button", { name: `Pin ${AGENT_NAME}` })
				.click();
			expect((await pinnedSaved).ok()).toBe(true);
			await expect(
				page.getByRole("button", { name: `Unpin ${AGENT_NAME}` }),
			).toHaveAttribute("aria-pressed", "true");

			await page.goto("/");
			await page.reload();
			const browse = page.getByRole("region", {
				name: "Browse resources",
			});
			await expect(
				browse.getByRole("button", { name: AGENT_NAME, exact: true }),
			).toBeVisible({ timeout: 10000 });
			await expect(
				browse.getByRole("button", { name: `Unpin ${AGENT_NAME}` }),
			).toHaveAttribute("aria-pressed", "true");

			const unpinnedSaved = page.waitForResponse(
				(response) =>
					response.url().includes("/api/home/preferences/") &&
					response.request().method() === "PUT",
			);
			await browse
				.getByRole("button", { name: `Unpin ${AGENT_NAME}` })
				.click();
			expect((await unpinnedSaved).ok()).toBe(true);

			await page.goto("/");
			await page.reload();
			await page
				.getByRole("textbox", { name: "Search Home resources" })
				.fill(AGENT_NAME);
			await expect(
				browse.getByRole("button", { name: AGENT_NAME, exact: true }),
			).toBeVisible({ timeout: 10000 });
			await expect(
				browse.getByRole("button", { name: `Pin ${AGENT_NAME}` }),
			).toHaveAttribute("aria-pressed", "false");

			await page
				.getByRole("textbox", { name: "Search Home resources" })
				.fill(AGENT_NAME);
			const conversationCreated = page.waitForResponse(
				(response) =>
					response.url().includes("/api/chat/conversations") &&
					response.request().method() === "POST",
			);
			await page
				.getByRole("button", { name: AGENT_NAME, exact: true })
				.click();
			const conversationResponse = await conversationCreated;
			expect(
				conversationResponse.ok(),
				await conversationResponse.text(),
			).toBe(true);
			const conversation = (await conversationResponse.json()) as {
				id: string;
			};
			conversationId = conversation.id;
			await expect(page).toHaveURL(
				new RegExp(`/chat/${conversation.id}$`),
			);
		} finally {
			if (conversationId) {
				await expectDeleted(
					await api.delete(
						`/api/chat/conversations/${conversationId}`,
					),
				);
			}
			await expectDeleted(await api.delete(`/api/agents/${agent.id}`));
		}
	});
});
