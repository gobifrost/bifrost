import { test, expect } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-publish-notification-${UNIQUE}`;
const APP_NAME = `E2E Publish Notification ${UNIQUE}`;

test.describe("Application publish notifications", () => {
	let appId = "";

	test.beforeAll(async ({ api }) => {
		const response = await api.post("/api/applications", {
			data: {
				name: APP_NAME,
				slug: APP_SLUG,
				app_model: "inline_v1",
			},
		});
		expect(response.ok(), await response.text()).toBe(true);
		appId = (await response.json()).id;
	});

	test.afterAll(async ({ api }) => {
		if (appId) await api.delete(`/api/applications/${appId}`);
	});

	test("queues in the dialog and reports completion through notifications", async ({
		page,
	}) => {
		await page.goto(`/apps/${APP_SLUG}/edit`);
		await page.getByRole("button", { name: "Publish" }).click();
		const dialog = page.getByRole("dialog", { name: "Publish Application" });
		await dialog.getByLabel("Publish Message (optional)").fill("WebSocket release");
		await dialog.getByRole("button", { name: "Publish" }).click();

		await expect(dialog).toBeHidden();
		await expect(page.getByText("Application publish queued")).toBeVisible();

		await page.getByRole("button", { name: "Notifications" }).click();
		const notification = page
			.getByText(`Publishing ${APP_NAME}`, { exact: true })
			.first()
			.locator("..");
		await expect(notification).toBeVisible();
		await expect(
			notification.getByText("Completed", { exact: true }),
		).toBeVisible({
			timeout: 30_000,
		});
	});
});
