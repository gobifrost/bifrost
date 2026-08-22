import { expect, test } from "./fixtures/api-fixture";

test.describe("Detail route loading", () => {
	test("keeps navigation continuous and gives direct loads immediate feedback", async ({
		page,
		api,
	}, testInfo) => {
		const name = `Route Ready Agent ${Date.now()}`;
		const createResponse = await api.post("/api/agents", {
			data: {
				name,
				description: "Exercises the route-ready loading boundary.",
				system_prompt: "You are a route loading test agent.",
				channels: ["chat"],
				access_level: "authenticated",
			},
		});
		expect(createResponse.ok(), await createResponse.text()).toBe(true);
		const agent = (await createResponse.json()) as { id: string };
		const detailPattern = `**/api/agents/${agent.id}`;

		try {
			await page.goto("/agents");
			const fleetHeading = page
				.getByRole("heading", { name: /agents/i })
				.first();
			await expect(fleetHeading).toBeVisible();
			const card = page.getByRole("link").filter({ hasText: name });
			await expect(card).toBeVisible();

			let releaseNavigation!: () => void;
			const navigationGate = new Promise<void>((resolve) => {
				releaseNavigation = resolve;
			});
			let detailRequests = 0;
			await page.route(detailPattern, async (route) => {
				detailRequests += 1;
				await navigationGate;
				await route.continue();
			});

			const click = card.click();
			await expect(
				page.getByRole("progressbar", { name: "Loading page" }),
			).toBeVisible();
			await expect(fleetHeading).toBeVisible();
			expect(new URL(page.url()).pathname).toBe("/agents");

			releaseNavigation();
			await click;
			await expect(page).toHaveURL(new RegExp(`/agents/${agent.id}$`));
			await expect(page.getByRole("heading", { name })).toBeVisible();
			await expect(page.getByText("Loading…")).toHaveCount(0);
			expect(detailRequests).toBe(1);

			await page.unroute(detailPattern);
			let releaseDirectLoad!: () => void;
			const directLoadGate = new Promise<void>((resolve) => {
				releaseDirectLoad = resolve;
			});
			await page.route(detailPattern, async (route) => {
				await directLoadGate;
				await route.continue();
			});

			const reload = page.reload({ waitUntil: "domcontentloaded" });
			await expect(
				page.getByRole("status", { name: "Opening agent…" }),
			).toBeVisible();
			await testInfo.attach("detail-route-opening", {
				body: await page.screenshot({ fullPage: true }),
				contentType: "image/png",
			});

			releaseDirectLoad();
			await reload;
			await expect(page.getByRole("heading", { name })).toBeVisible();
			await testInfo.attach("detail-route-ready", {
				body: await page.screenshot({ fullPage: true }),
				contentType: "image/png",
			});
		} finally {
			await page.unroute(detailPattern);
			await api.delete(`/api/agents/${agent.id}`);
		}
	});
});
