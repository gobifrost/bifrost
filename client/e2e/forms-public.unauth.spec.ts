import { readFileSync } from "node:fs";
import { createHmac } from "node:crypto";
import { resolve } from "node:path";
import {
	test,
	expect,
	request as playwrightRequest,
	type APIRequestContext,
	type Locator,
} from "@playwright/test";

const API_URL = process.env.TEST_API_URL || "http://api:8000";
const BIFROST_URL = process.env.TEST_BASE_URL || "http://client:80";
const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const PROVIDER_PATH = `e2e_public_provider_${UNIQUE}.py`;
const PROVIDER_FN = `e2e_public_provider_${UNIQUE}`;
const SUBMIT_PATH = `e2e_public_submit_${UNIQUE}.py`;
const SUBMIT_FN = `e2e_public_submit_${UNIQUE}`;
const HMAC_SECRET = `hmac-${UNIQUE}`;

const PROVIDER_SOURCE = `from bifrost import data_provider

@data_provider(name="${PROVIDER_FN}")
async def ${PROVIDER_FN}():
    return [
        {
            "value": "acme",
            "label": "Acme Corporation",
            "metadata": {"company_name": "Acme Corporation", "private": "hidden"},
        }
    ]
`;

const SUBMIT_SOURCE = `from bifrost import workflow

@workflow(name="${SUBMIT_FN}")
async def ${SUBMIT_FN}(
    company: str,
    email: str,
    company_name: str = "",
    company_size: str = "",
    referral_source: str = "",
):
    return {
        "company": company,
        "email": email,
        "company_name": company_name,
        "company_size": company_size,
        "referral_source": referral_source,
    }
`;

async function expectOk(
	response: Awaited<ReturnType<APIRequestContext["fetch"]>>,
) {
	expect(response.ok(), await response.text()).toBe(true);
}

test.describe.serial("Public form iframe", () => {
	let api: APIRequestContext;
	let formId: string;
	let publicKey: string;
	let scrollFormId: string;
	let scrollPublicKey: string;

	test.beforeAll(async () => {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as { platform_admin: { accessToken: string } };
		api = await playwrightRequest.newContext({
			baseURL: API_URL,
			extraHTTPHeaders: {
				Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
			},
		});

		for (const [path, content, functionName] of [
			[PROVIDER_PATH, PROVIDER_SOURCE, PROVIDER_FN],
			[SUBMIT_PATH, SUBMIT_SOURCE, SUBMIT_FN],
		] as const) {
			const write = await api.put("/api/files/editor/content", {
				data: { path, content, encoding: "utf-8" },
			});
			await expectOk(write);
			const registration = await api.post("/api/workflows/register", {
				data: { path, function_name: functionName },
			});
			await expectOk(registration);
		}

		const providers = await api.get("/api/workflows", {
			params: { type: "data_provider" },
		});
		await expectOk(providers);
		const provider = (
			(await providers.json()) as Array<{ id: string; name: string }>
		).find((item) => item.name === PROVIDER_FN);
		expect(provider).toBeTruthy();

		const workflows = await api.get("/api/workflows");
		await expectOk(workflows);
		const workflow = (
			(await workflows.json()) as Array<{ id: string; name: string }>
		).find((item) => item.name === SUBMIT_FN);
		expect(workflow).toBeTruthy();

		const created = await api.post("/api/forms", {
			data: {
				name: `Public website form ${UNIQUE}`,
				description: "A live anonymous iframe test",
				workflow_id: workflow!.id,
				confirmation_markdown:
					"## Thank you\n\n**Your form was submitted.**\n\n![Bifrost mark](/vite.svg)",
				form_schema: {
					fields: [
						{
							name: "company",
							label: "Company",
							type: "select",
							required: true,
							data_provider_id: provider!.id,
							auto_fill: { company_name: "company_name" },
						},
						{
							name: "company_name",
							label: "Company name",
							type: "text",
						},
						{
							name: "email",
							label: "Email",
							type: "email",
							required: true,
						},
					],
				},
			},
		});
		await expectOk(created);
		formId = ((await created.json()) as { id: string }).id;

		const scrollForm = await api.post("/api/forms", {
			data: {
				name: `Embedded scroll regression ${UNIQUE}`,
				workflow_id: workflow!.id,
				form_schema: {
					fields: [
						{
							name: "company",
							label: "Company",
							type: "select",
							required: true,
							data_provider_id: provider!.id,
							auto_fill: { company_name: "company_name" },
						},
						{
							name: "company_name",
							label: "Company name",
							type: "text",
						},
						{
							name: "company_size",
							label: "Company size",
							type: "select",
							options: [
								{ value: "small", label: "1–10 people" },
								{ value: "medium", label: "11–100 people" },
								{ value: "large", label: "101+ people" },
							],
						},
						{
							name: "email",
							label: "Email",
							type: "email",
							required: true,
						},
						{
							name: "referral_source",
							label: "How did you hear about us",
							type: "select",
							options: [
								{ value: "search", label: "Search engine" },
								{ value: "referral", label: "Referral" },
								{ value: "event", label: "Event" },
							],
						},
					],
				},
			},
		});
		await expectOk(scrollForm);
		scrollFormId = ((await scrollForm.json()) as { id: string }).id;

		const scrollReview = await api.get(
			`/api/forms/${scrollFormId}/publication-review`,
		);
		await expectOk(scrollReview);
		const review = (await scrollReview.json()) as { fingerprint: string };
		const scrollPublication = await api.put(
			`/api/forms/${scrollFormId}/publication`,
			{
				data: {
					reviewed_fingerprint: review.fingerprint,
					allowed_origins: ["http://allowed-origin"],
				},
			},
		);
		await expectOk(scrollPublication);
		scrollPublicKey = (
			(await scrollPublication.json()) as { public_key: string }
		).public_key;
	});

	test.afterAll(async () => {
		if (!api) return;
		if (scrollFormId) await api.delete(`/api/forms/${scrollFormId}`);
		if (formId) await api.delete(`/api/forms/${formId}`);
		await api.delete(
			`/api/files/editor?path=${encodeURIComponent(PROVIDER_PATH)}`,
		);
		await api.delete(
			`/api/files/editor?path=${encodeURIComponent(SUBMIT_PATH)}`,
		);
		await api.dispose();
	});

	test("publishes in the UI and submits from an allowed second origin", async ({
		browser,
		page,
	}) => {
		const admin = await browser.newContext({
			baseURL: BIFROST_URL,
			storageState: "e2e/.auth/platform_admin.json",
		});
		const adminPage = await admin.newPage();
		await adminPage.goto(`/forms/${formId}/edit`);
		await adminPage.getByTitle("Share Form").click();
		await expect(adminPage.getByLabel("Private form link")).toHaveValue(
			`${new URL(BIFROST_URL).origin}/execute/${formId}`,
		);
		await expect(
			adminPage.getByRole("heading", { name: "Confirmation Message" }),
		).toHaveCount(0);
		await adminPage.getByRole("tab", { name: "HMAC" }).click();
		await expect(
			adminPage.getByText("No embed secrets configured."),
		).toBeVisible();
		await expect(
			adminPage.getByRole("heading", { name: "Confirmation Message" }),
		).toHaveCount(0);
		await adminPage.getByRole("tab", { name: "Website Embed" }).click();
		await expect(
			adminPage.getByRole("switch", { name: "Spam Protection" }),
		).toBeChecked();
		await expect(
			adminPage.getByRole("heading", { name: "Confirmation Message" }),
		).toBeVisible();
		await expect(
			adminPage.getByLabel("Confirmation Message editor"),
		).toContainText("Thank you");
		await adminPage
			.getByLabel("Confirmation Message editor")
			.fill("## Preview check\n\nThis should render immediately.");
		await adminPage.getByRole("tab", { name: "Preview" }).click();
		const previewHeading = adminPage.getByRole("heading", {
			name: "Preview check",
		});
		await expect(previewHeading).toBeVisible();
		const previewPanel = adminPage.getByRole("tabpanel", {
			name: "Preview",
		});
		const previewBody = previewPanel.getByText(
			"This should render immediately.",
		);
		await expect(previewBody).toBeVisible();
		const headingFontSize = await previewHeading.evaluate((element) =>
			Number.parseFloat(window.getComputedStyle(element).fontSize),
		);
		const bodyFontSize = await previewBody.evaluate((element) =>
			Number.parseFloat(window.getComputedStyle(element).fontSize),
		);
		expect(headingFontSize).toBeGreaterThan(bodyFontSize);
		await adminPage.getByRole("tab", { name: "Edit" }).click();
		await adminPage
			.getByRole("button", { name: /Website Restrictions/ })
			.click();
		await adminPage
			.getByLabel("Allowed Website Origins")
			.fill("http://allowed-origin");
		await adminPage.getByRole("switch", { name: "Not Published" }).click();
		await expect(
			adminPage.getByRole("heading", {
				name: "Allow anonymous form access?",
			}),
		).toBeVisible();
		await expect(
			adminPage.getByText(
				/No other workflows or Bifrost execution APIs are granted/i,
			),
		).toBeVisible();
		await adminPage
			.getByRole("button", { name: "Publish public embed" })
			.click();
		await expect(
			adminPage.getByText("Published", { exact: true }),
		).toBeVisible();
		const embedCode = adminPage.getByLabel("Embed Code");
		await expect(embedCode).toContainText(
			"theme=light&header=true&background=solid",
		);
		await adminPage.getByRole("combobox", { name: "Theme" }).click();
		await adminPage.getByRole("option", { name: "Dark" }).click();
		await adminPage.getByRole("switch", { name: "Show Header" }).click();
		await adminPage
			.getByRole("switch", { name: "Transparent Background" })
			.click();
		await expect(embedCode).toContainText(
			"theme=dark&header=false&background=transparent",
		);
		await admin.close();

		const publication = await api.get(`/api/forms/${formId}/publication`);
		await expectOk(publication);
		publicKey = ((await publication.json()) as { public_key: string })
			.public_key;

		const forbiddenRequests: string[] = [];
		let documentCsp: string | null = null;
		let submissionBody: Record<string, unknown> | null = null;
		page.on("request", (request) => {
			const pathname = new URL(request.url()).pathname;
			if (
				pathname === "/api/workflows/execute" ||
				pathname.startsWith("/api/executions/") ||
				pathname === "/ws"
			) {
				forbiddenRequests.push(pathname);
			}
		});
		page.on("response", async (response) => {
			const pathname = new URL(response.url()).pathname;
			if (pathname === `/embedded/forms/public/${publicKey}`) {
				documentCsp =
					response.headers()["content-security-policy"] || null;
			}
			if (pathname === `/api/forms/${formId}/submissions`) {
				submissionBody = (await response.json()) as Record<
					string,
					unknown
				>;
			}
		});

		// Load a real document from the second Docker-network origin so Chromium
		// classifies both hosts in the same local address space. Block only that
		// parent's SPA scripts, then replace its HTML with the customer iframe.
		// The embedded client uses the separate `client` host and remains intact.
		await page.route("http://allowed-origin/**", (route) =>
			route.request().resourceType() === "script"
				? route.abort()
				: route.continue(),
		);
		await page.goto("http://allowed-origin/");
		await page.setContent(
			`<iframe title="Public form" style="width:100%;height:800px" src="${BIFROST_URL}/embed/forms/public/${publicKey}"></iframe>`,
		);
		const frame = page.frameLocator('iframe[title="Public form"]');
		await expect(
			frame.getByRole("heading", {
				name: `Public website form ${UNIQUE}`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await frame
			.getByRole("combobox", { name: "Company *", exact: true })
			.click();
		await frame.getByText("Acme Corporation", { exact: true }).click();
		await expect(frame.getByLabel("Company name")).toHaveValue(
			"Acme Corporation",
		);
		await frame.getByLabel("Email").fill("visitor@example.com");
		const submit = frame.getByRole("button", { name: "Submit" });
		await expect(submit).toBeDisabled();
		await frame.getByRole("checkbox", { name: "I'm not a robot" }).click();
		await expect(
			frame.getByText("Verified", { exact: true }),
		).toBeVisible();
		await expect(submit).toBeEnabled();
		await submit.click();

		const confirmation = frame.getByRole("status");
		await expect(confirmation).toBeVisible();
		await expect(
			frame.getByRole("heading", { name: "Thank you" }),
		).toBeVisible();
		await expect(frame.getByText("Your form was submitted.")).toBeVisible();
		await expect(
			frame.getByRole("img", { name: "Bifrost mark" }),
		).toHaveAttribute("referrerpolicy", "no-referrer");
		expect(
			await confirmation.evaluate(
				(element) => element === document.activeElement,
			),
		).toBe(true);
		expect(documentCsp).toBe("frame-ancestors http://allowed-origin");
		expect(submissionBody).toEqual({
			mode: "confirmation",
			status: "accepted",
			confirmation_markdown:
				"## Thank you\n\n**Your form was submitted.**\n\n![Bifrost mark](/vite.svg)",
		});
		expect(forbiddenRequests).toEqual([]);
		await expect(frame.getByText(/execution|history/i)).toHaveCount(0);
		await page.screenshot({
			path: "playwright-results/public-form-confirmation.png",
			fullPage: true,
		});

		const hmacSecret = await api.post(
			`/api/forms/${formId}/embed-secrets`,
			{
				data: { name: "Browser result test", secret: HMAC_SECRET },
			},
		);
		await expectOk(hmacSecret);
	});

	test("keeps the host page fixed while opening consecutive form dropdowns", async ({
		page,
	}) => {
		await page.route("http://allowed-origin/**", (route) =>
			route.request().resourceType() === "script"
				? route.abort()
				: route.continue(),
		);
		await page.goto("http://allowed-origin/");
		await page.setContent(`
			<div style="height:1200px"></div>
			<iframe
				title="Bottom form"
				style="display:block;width:100%;height:900px;border:0"
				src="${BIFROST_URL}/embed/forms/public/${scrollPublicKey}"
			></iframe>
		`);

		const frame = page.frameLocator('iframe[title="Bottom form"]');
		await expect(
			frame.getByRole("heading", {
				name: `Embedded scroll regression ${UNIQUE}`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await page.evaluate(() =>
			window.scrollTo(0, document.body.scrollHeight),
		);
		const bottom = await page.evaluate(() => window.scrollY);
		const clickVisibleControl = async (control: Locator) => {
			const bounds = await control.boundingBox();
			expect(bounds).not.toBeNull();
			expect(bounds!.y).toBeGreaterThanOrEqual(0);
			expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(
				page.viewportSize()!.height,
			);
			await page.mouse.click(
				bounds!.x + bounds!.width / 2,
				bounds!.y + bounds!.height / 2,
			);
		};
		const expectParentToRemainFixed = async () => {
			const positions = await page.evaluate(async () => {
				const samples: number[] = [];
				for (let frame = 0; frame < 5; frame += 1) {
					await new Promise<void>((resolve) =>
						requestAnimationFrame(() => resolve()),
					);
					samples.push(window.scrollY);
				}
				return samples;
			});
			expect(positions).toEqual(Array(5).fill(bottom));
		};

		await clickVisibleControl(
			frame.getByRole("combobox", { name: "Company size" }),
		);
		await expect(
			frame.getByRole("option", { name: "1–10 people" }),
		).toBeVisible();
		await expectParentToRemainFixed();
		await page.keyboard.press("Escape");

		await clickVisibleControl(
			frame.getByRole("combobox", {
				name: "How did you hear about us",
			}),
		);
		await expect(
			frame.getByRole("option", { name: "Search engine" }),
		).toBeVisible();
		await expectParentToRemainFixed();
	});

	test("shows only the signed session's execution result after an HMAC submission", async ({
		page,
	}) => {
		const signedParams = { agent_id: "42" };
		const message = Object.entries(signedParams)
			.sort(([left], [right]) => left.localeCompare(right))
			.map(([key, value]) => `${key}=${value}`)
			.join("&");
		const signature = createHmac("sha256", HMAC_SECRET)
			.update(message)
			.digest("hex");

		await page.route("http://allowed-origin/**", (route) =>
			route.request().resourceType() === "script"
				? route.abort()
				: route.continue(),
		);
		await page.goto("http://allowed-origin/");
		await page.setContent(
			`<iframe title="HMAC form" style="width:100%;height:900px" src="${BIFROST_URL}/embed/forms/${formId}?agent_id=42&hmac=${signature}"></iframe>`,
		);
		const frame = page.frameLocator('iframe[title="HMAC form"]');
		await expect(
			frame.getByRole("heading", {
				name: `Public website form ${UNIQUE}`,
			}),
		).toBeVisible();
		await frame
			.getByRole("combobox", { name: "Company *", exact: true })
			.click();
		await frame.getByText("Acme Corporation", { exact: true }).click();
		await frame.getByLabel("Email").fill("hmac@example.com");
		await expect(
			frame.getByRole("checkbox", { name: "I'm not a robot" }),
		).toHaveCount(0);
		await frame.getByRole("button", { name: "Submit" }).click();

		await expect
			.poll(
				() =>
					page
						.frames()
						.some((candidate) =>
							/\/history\/[0-9a-f-]{36}$/.test(candidate.url()),
						),
				{ timeout: 20_000 },
			)
			.toBe(true);
		await expect(
			frame.getByRole("heading", { name: "Result" }),
		).toBeVisible({
			timeout: 30_000,
		});
		await expect(frame.getByText("hmac@example.com").first()).toBeVisible();
		await expect(frame.getByRole("status")).toHaveCount(0);
	});

	test("blocks a disallowed browser ancestor on the final document", async ({
		page,
	}) => {
		await page.route("http://blocked-origin/**", (route) =>
			route.request().resourceType() === "script"
				? route.abort()
				: route.continue(),
		);
		const blocked = page.waitForEvent("console", {
			predicate: (message) =>
				message.type() === "error" &&
				message.text().includes("frame-ancestors"),
		});
		await page.goto("http://blocked-origin/");
		await page.setContent(
			`<iframe title="Blocked form" src="${BIFROST_URL}/embed/forms/public/${publicKey}"></iframe>`,
		);
		await blocked;
		await expect(
			page
				.frameLocator('iframe[title="Blocked form"]')
				.getByRole("heading", {
					name: `Public website form ${UNIQUE}`,
				}),
		).toHaveCount(0);
	});
});
