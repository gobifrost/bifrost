import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("Studio freezes a case, compares an isolated candidate, and opens its evidence", async ({
	page,
	api,
}, testInfo) => {
	let agentId: string | undefined;
	try {
		const created = await api.post("/api/agents", {
			data: {
				name: `Studio ${randomUUID()}`,
				system_prompt: "Reply only with ok.",
				access_level: "private",
				system_tools: [],
				channels: [],
			},
		});
		expect(created.ok()).toBe(true);
		const agent = await created.json();
		agentId = agent.id;
		await page.goto(`/agents/${agentId}`);
		await page.getByRole("link", { name: "Evaluation Studio" }).click();
		await page.getByText("Create a suite", { exact: true }).click();
		await page.getByLabel("Suite name").fill("Response regression");
		await page
			.getByLabel("Description", { exact: true })
			.fill("Frozen deterministic response check");
		await page
			.getByRole("button", { name: "Create suite", exact: true })
			.click();
		await page.getByRole("button", { name: "Author case" }).click();
		await page.getByLabel("Case name").fill("Simple response");
		await page
			.getByLabel("Invocation input (JSON)")
			.fill('{"message":"Return the fixture answer."}');
		await page.getByRole("button", { name: "Freeze and add case" }).click();
		await expect(
			page.getByRole("heading", { name: "Simple response v1" }),
		).toBeVisible();
		await page.getByRole("tab", { name: "Candidate", exact: true }).click();
		await page.getByLabel("Candidate name").fill("Concise response");
		await page
			.getByLabel("Candidate prompt · evaluation only")
			.fill("Reply with ok and nothing else.");
		await page
			.getByRole("button", { name: "Create immutable candidate" })
			.click();
		await expect(
			page.getByRole("heading", {
				name: "Concise response",
				exact: true,
			}),
		).toBeVisible();
		expect(
			(await (await api.get(`/api/agents/${agentId}`)).json())
				.system_prompt,
		).toBe("Reply only with ok.");
		await page
			.getByRole("button", { name: "Review production diff" })
			.click();
		await expect(page.getByLabel("Live system_prompt")).toHaveText(
			'"Reply only with ok."',
		);
		await page
			.getByRole("heading", { name: "Apply candidate to live Agent" })
			.scrollIntoViewIfNeeded();
		await page
			.locator("section")
			.filter({
				has: page.getByRole("heading", {
					name: "Apply candidate to live Agent",
				}),
			})
			.screenshot({
				path: testInfo.outputPath("studio-candidate-diff.png"),
			});
		await page.getByRole("button", { name: "Cancel", exact: true }).click();
		await page
			.getByRole("button", { name: "Publish frozen suite" })
			.click();
		await page
			.getByRole("button", { name: "Confirm publish suite" })
			.click();
		// Wait for the durable execution response within the existing test deadline.
		// The generic 5s visibility assertion is not the execution lifecycle.
		const completed = page.waitForResponse(async (response) => {
			if (
				!/\/api\/agent-evaluations\/executions\/[^/?]+$/.test(
					response.url(),
				) ||
				response.request().method() !== "GET" ||
				!response.ok()
			)
				return false;
			const execution = await response.json();
			return ["succeeded", "failed", "cancelled"].includes(
				execution.status,
			);
		});
		await page
			.getByRole("button", { name: "Run baseline and candidate" })
			.click();
		await completed;
		await expect(
			page.getByText("1 / 1 completed · 1 passed · 0 failed"),
		).toBeVisible();
		await expect(
			page.getByRole("columnheader", { name: "Baseline", exact: true }),
		).toBeVisible();
		await expect(
			page.getByRole("columnheader", { name: "Candidate", exact: true }),
		).toBeVisible();
		await page.getByRole("article").scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("studio-results-desktop.png"),
		});
		await page
			.getByRole("heading", { name: "Assertions", exact: true })
			.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("studio-evidence-desktop.png"),
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page
			.getByRole("article")
			.getByRole("heading")
			.first()
			.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("studio-results-mobile.png"),
		});
		await page
			.getByRole("link", { name: "Debug candidate run" })
			.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("studio-evidence-mobile.png"),
		});
		await page.getByRole("link", { name: "Debug candidate run" }).click();
		await expect(
			page.getByRole("heading", { name: "Run debugger" }),
		).toBeVisible();
		expect(
			(await (await api.get(`/api/agents/${agentId}`)).json())
				.system_prompt,
		).toBe("Reply only with ok.");
	} finally {
		if (agentId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
	}
});
