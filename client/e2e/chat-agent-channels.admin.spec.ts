import { expect, test } from "./fixtures/api-fixture";

test("Chat discovery respects agent channels without removing API access", async ({
	page,
	api,
}) => {
	await page.route("**/api/chat/model-profiles", (route) =>
		route.fulfill({
			json: {
				profiles: [{ id: "test-profile", name: "Test", label: "Test" }],
				default_profile_id: "test-profile",
			},
		}),
	);
	const name = `ChannelAgent${Date.now()}`;
	const created = await api.post("/api/agents", {
		data: {
			name,
			system_prompt: "Channel discovery test.",
			access_level: "authenticated",
			channels: [],
		},
	});
	expect(created.ok(), await created.text()).toBe(true);
	const agent = await created.json();
	try {
		await page.goto("/chat");
		const input = page.getByRole("textbox", { name: "Chat input" });
		await input.fill(`@${name}`);
		await expect(page.getByText("No agents found")).toBeVisible();
		expect((await api.get(`/api/agents/${agent.id}`)).ok()).toBe(true);
		const updated = await api.put(`/api/agents/${agent.id}`, {
			data: { channels: ["chat"] },
		});
		expect(updated.ok(), await updated.text()).toBe(true);
		await page.reload();
		await input.fill(`@${name}`);
		await expect(page.getByText(name, { exact: true })).toBeVisible();
	} finally {
		expect((await api.delete(`/api/agents/${agent.id}`)).ok()).toBe(true);
	}
});
