/** Real seeded fleet browsing; no empty-state fallback can satisfy an action. */
import { test, expect } from "./fixtures/api-fixture";

test.describe("Agents Fleet Page (admin)", () => {
	test("[AGENT-FILTER-01 desktop] inactive agents appear only when requested", async ({
		page,
		api,
	}) => {
		const name = `Inactive Fleet Agent ${Date.now()}`;
		const createResponse = await api.post("/api/agents", {
			data: {
				name,
				system_prompt:
					"You are an inactive fleet visibility test agent.",
				access_level: "authenticated",
			},
		});
		expect(createResponse.ok(), await createResponse.text()).toBe(true);
		const agent = await createResponse.json();

		try {
			const deactivateResponse = await api.put(
				`/api/agents/${agent.id}`,
				{
					data: { is_active: false },
				},
			);
			expect(
				deactivateResponse.ok(),
				await deactivateResponse.text(),
			).toBe(true);

			await page.goto("/agents");
			await expect(
				page.getByRole("heading", { name: /agents/i }).first(),
			).toBeVisible({ timeout: 10000 });
			await page
				.getByRole("textbox", { name: "Search agents", exact: true })
				.fill(name);
			await expect(page.getByText(name)).toHaveCount(0);

			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(page.getByText(name)).toBeVisible();
		} finally {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agent.id}`)).status(),
			);
		}
	});

	test("[AGENT-BROWSE-01 desktop] search finds a seeded agent and grid and table open the same detail", async ({
		page,
		api,
	}) => {
		const name = `Browse Fleet Agent ${Date.now()}`;
		const response = await api.post("/api/agents", {
			data: {
				name,
				description: "Known fleet browse acceptance record",
				system_prompt: "You are a synthetic browse test agent.",
				access_level: "authenticated",
				channels: ["chat"],
			},
		});
		expect(response.ok(), await response.text()).toBe(true);
		const agent = (await response.json()) as { id: string };
		try {
			await page.goto("/agents");
			const statistics = page.getByRole("region", {
				name: "Fleet statistics",
			});
			await expect(statistics).toHaveAttribute("aria-busy", "false");
			await expect(
				statistics.getByText("Runs (7d)", { exact: true }),
			).toBeVisible();
			const search = page.getByRole("textbox", {
				name: "Search agents",
				exact: true,
			});
			await search.fill(name);
			const card = page
				.getByRole("article")
				.filter({ has: page.getByRole("link", { name, exact: true }) });
			await expect(card).toHaveCount(1);
			await expect(card).toContainText(
				"Known fleet browse acceptance record",
			);
			await card.getByRole("link", { name, exact: true }).click();
			await expect(page).toHaveURL(new RegExp(`/agents/${agent.id}$`));
			await expect(
				page
					.getByRole("heading", { level: 1 })
					.getByText(name, { exact: true }),
			).toBeVisible();

			await page.goto("/agents");
			await search.fill(name);
			await page
				.getByRole("button", { name: "Table view", exact: true })
				.click();
			await expect(
				page.getByRole("button", { name: "Table view", exact: true }),
			).toHaveAttribute("aria-pressed", "true");
			const row = page
				.getByRole("row")
				.filter({ has: page.getByRole("link", { name, exact: true }) });
			await expect(row).toHaveCount(1);
			// Click row description, away from its nested link, to exercise row opening.
			await row
				.getByText("Known fleet browse acceptance record", {
					exact: true,
				})
				.click();
			await expect(page).toHaveURL(new RegExp(`/agents/${agent.id}$`));
			await expect(
				page
					.getByRole("heading", { level: 1 })
					.getByText(name, { exact: true }),
			).toBeVisible();

			await page.goto("/agents");
			await search.fill(`${name} absent`);
			await expect(
				page.getByRole("heading", {
					name: "No agents match your search",
					exact: true,
				}),
			).toBeVisible();
			await expect(
				page.getByRole("link", { name, exact: true }),
			).toHaveCount(0);
		} finally {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agent.id}`)).status(),
			);
		}
	});
});
