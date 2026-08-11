/**
 * Private app-builder browser happy path.
 *
 * Creation and session wiring are exercised against the real API. The model
 * turn itself is intercepted because Playwright environments intentionally do
 * not carry an external LLM credential; the backend Agent/build/deploy path is
 * covered by the dedicated API E2E suites.
 */

import { test, expect } from "./fixtures/api-fixture";

async function exposeConfiguredBuilder(page: import("@playwright/test").Page) {
	await page.route("**/api/builder/solutions", async (route) => {
		if (route.request().method() !== "GET") {
			await route.continue();
			return;
		}
		const response = await route.fetch();
		const body = (await response.json()) as Record<string, unknown>;
		await route.fulfill({
			response,
			json: {
				...body,
				ai_configured: true,
				builder_ready: true,
				builder_blockers: [],
			},
		});
	});
}

test("guides an admin through Builder setup before Build is configured", async ({
	page,
}) => {
	await page.goto("/build");

	await expect(
		page.getByRole("heading", { name: "Finish connecting Builder" }),
	).toBeVisible({ timeout: 10_000 });
	await expect(page.getByText("AI provider configuration is required")).toBeVisible();
	await expect(
		page.getByText("No sandbox runner provider has been configured."),
	).toBeVisible();

	await page.getByRole("button", { name: "Open Builder setup" }).click();
	await expect(page).toHaveURL(/\/settings\/builder$/);
	await expect(
		page.getByRole("heading", { name: "Native app building" }),
	).toBeVisible();
	await page.getByRole("link", { name: "Configure AI" }).click();
	await expect(page).toHaveURL(/\/settings\/ai$/);
});

test("creates a private app workspace from /build", async ({ page, api }) => {
	test.setTimeout(60000);
	const suffix = Date.now().toString(36);
	const name = `E2E Expense Tracker ${suffix}`;
	const prompt = "Build an expense tracker with category filters.";
	let solutionId: string | null = null;

	await exposeConfiguredBuilder(page);
	await page.route("**/api/builder/solutions/*/turns", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		await route.fulfill({
			status: 503,
			contentType: "application/json",
			body: JSON.stringify({
				detail: "Model execution is disabled in browser E2E",
			}),
		});
	});

	await page.goto("/build");
	await expect(
		page.getByRole("heading", { name: "What should Bifrost build?" }),
	).toBeVisible({ timeout: 10000 });

	await page.getByRole("textbox", { name: "App name" }).fill(name);
	await page.getByRole("textbox", { name: "Describe your app" }).fill(prompt);

	const turnRequest = page.waitForRequest(
		(request) =>
			request.method() === "POST" &&
			/\/api\/builder\/solutions\/[^/]+\/turns$/.test(request.url()),
	);
	await page.getByRole("button", { name: "Start building" }).click();

	await expect(page).toHaveURL(/\/solutions\/[0-9a-f-]+\/builder$/, {
		timeout: 15000,
	});
	solutionId =
		page.url().match(/\/solutions\/([0-9a-f-]+)\/builder$/)?.[1] ?? null;
	expect(solutionId).not.toBeNull();

	const request = await turnRequest;
	expect(request.postDataJSON()).toMatchObject({ message: prompt });

	await expect(page.getByRole("heading", { name })).toBeVisible();
	await expect(page.getByText("Private", { exact: true })).toBeVisible();
	await expect(
		page.getByText("bifrost-build guides each generated change", {
			exact: true,
		}),
	).toBeVisible();
	await expect(
		page.getByText("bifrost-build", { exact: true }).first(),
	).toBeVisible();
	await expect(page.getByText("Preview is not deployed yet")).toBeVisible();

	await page.getByRole("tab", { name: "Code" }).click();
	await expect(
		page.getByRole("textbox", { name: "Find a source file" }),
	).toBeVisible();
	await expect(page.getByText("bifrost.solution.yaml").first()).toBeVisible();
	await expect(page.getByTestId("builder-code-content")).toContainText(
		"bifrost-build",
	);

	await page.getByRole("tab", { name: /Changes/ }).click();
	await expect(page.getByText("Revision history")).toBeVisible();
	await expect(page.getByText("initial source")).toBeVisible();
	await expect(page.getByText("bifrost.solution.yaml").first()).toBeVisible();

	await page.getByRole("tab", { name: "Preview" }).click();
	await expect(page.getByText("Preview is not deployed yet")).toBeVisible();

	const response = await api.delete(`/api/builder/solutions/${solutionId}`);
	expect(response.ok()).toBe(true);
});

test("opens the admin Global Workspace as a proposal-only workbench", async ({
	page,
}) => {
	test.setTimeout(60_000);
	await exposeConfiguredBuilder(page);

	await page.goto("/build");
	await expect(
		page.getByRole("heading", { name: "Global Workspace" }),
	).toBeVisible({ timeout: 10_000 });
	await expect(
		page.getByText(/nothing changes live until an administrator validates/i),
	).toBeVisible();

	await page
		.getByRole("button", { name: /^(?:Create|Open) Global Workspace$/ })
		.click();
	await expect(page).toHaveURL(/\/solutions\/[0-9a-f-]+\/builder$/, {
		timeout: 15_000,
	});

	await expect(
		page.getByRole("heading", { name: "Global Workspace" }),
	).toBeVisible();
	await expect(page.getByText("Live _repo", { exact: true })).toBeVisible();
	await expect(page.getByText("Admin only", { exact: true })).toBeVisible();
	await expect(
		page.getByText("Global Workspace agent", { exact: true }),
	).toBeVisible();
	await expect(
		page.getByText(/AI edits only the immutable proposal/i),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Apply to live" }),
	).toBeDisabled();
	await expect(page.getByRole("tab", { name: "Preview" })).toHaveCount(0);
	await expect(page.getByRole("button", { name: /open app/i })).toHaveCount(0);

	await page.getByRole("tab", { name: "Code" }).click();
	await expect(
		page.getByRole("textbox", { name: "Find a source file" }),
	).toBeVisible();
});

test("keeps Agent, Preview, Code, and Changes usable at mobile width", async ({
	page,
	api,
}) => {
	const suffix = Date.now().toString(36);
	const slug = `mobile-builder-${suffix}`;
	const create = await api.post("/api/builder/solutions", {
		data: { slug, name: `Mobile Builder ${suffix}` },
	});
	expect(create.ok()).toBe(true);
	const solution = (await create.json()) as { id: string };
	const session = await api.post(
		`/api/builder/solutions/${solution.id}/sessions`,
		{ data: { title: "Mobile review" } },
	);
	expect(session.ok()).toBe(true);

	try {
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/solutions/${solution.id}/builder`);

		await expect(
			page.getByRole("heading", { name: `Mobile Builder ${suffix}` }),
		).toBeVisible({ timeout: 10_000 });
		await expect(
			page.getByRole("button", { name: "Agent", exact: true }),
		).toBeVisible();
		await page.getByRole("button", { name: "Code", exact: true }).click();
		await expect(
			page.getByRole("textbox", { name: "Find a source file" }),
		).toBeVisible();

		await page.reload();
		await expect(
			page.getByRole("textbox", { name: "Find a source file" }),
		).toBeVisible();

		await page.getByRole("button", { name: "Changes" }).click();
		await expect(page.getByText("Revision history")).toBeVisible();
		await expect(page.getByText("initial source")).toBeVisible();

		await page.getByRole("button", { name: "Agent", exact: true }).click();
		await expect(
			page.getByText("Agent", { exact: true }).last(),
		).toBeVisible();
	} finally {
		const response = await api.delete(
			`/api/builder/solutions/${solution.id}`,
		);
		expect(response.ok()).toBe(true);
	}
});
