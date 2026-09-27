/** Persistent debug-only companion to the isolated review-pack acceptance test. */
import {
	chromium,
	expect,
	type APIRequestContext,
	type Page,
} from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { AuthedApi } from "../fixtures/api-fixture";
import { ensureReviewPack } from "../fixtures/review-pack";

async function main() {
	const { url, email, password, namespace, artifacts } = JSON.parse(
		readFileSync(0, "utf8"),
	) as {
		url: string;
		email: string;
		password: string;
		namespace: string;
		artifacts?: string;
	};
	const browser = await chromium.launch();
	let stage = "sign in";
	let page: Page | undefined;
	const networkFailures: { path: string; reason: string | null }[] = [];
	try {
		const context = await browser.newContext({ baseURL: url });
		page = await context.newPage();
		page.on("requestfailed", (request) =>
			networkFailures.push({
				path: new URL(request.url()).pathname,
				reason: request.failure()?.errorText ?? null,
			}),
		);
		await page.goto("/login");
		await page.getByLabel("Email", { exact: true }).fill(email);
		await page.getByLabel("Password", { exact: true }).fill(password);
		await page
			.getByRole("button", { name: "Sign In", exact: true })
			.click();
		await page.waitForURL((u) => !u.pathname.startsWith("/login"));
		const csrfHeader = async (): Promise<Record<string, string>> => {
			const csrf = (await context.cookies()).find(
				(cookie) => cookie.name === "csrf_token",
			);
			return csrf ? { "X-CSRF-Token": csrf.value } : {};
		};
		type Options = Parameters<APIRequestContext["get"]>[1];
		const send = async (method: string, path: string, options?: Options) =>
			context.request.fetch(path, {
				...options,
				method,
				headers: {
					...(method === "GET" ? {} : await csrfHeader()),
					...options?.headers,
				},
			});
		const api: AuthedApi = {
			get: (path, options) => send("GET", path, options),
			post: (path, options) => send("POST", path, options),
			put: (path, options) => send("PUT", path, options),
			patch: (path, options) => send("PATCH", path, options),
			delete: (path, options) => send("DELETE", path, options),
			csrfHeader,
		};
		stage = "ensure connected resources";
		const pack = await ensureReviewPack(api, namespace, {
			cleanupOnFailure: false,
		});
		stage = "verify repeatable resource identities";
		const again = await ensureReviewPack(api, namespace, {
			cleanupOnFailure: false,
		});
		if (JSON.stringify(pack.ids) !== JSON.stringify(again.ids))
			throw new Error("Resource identities changed");
		let review:
			{ formExecutionUrl: string; eventExecutionUrl: string } | undefined;
		if (artifacts) {
			mkdirSync(artifacts, { recursive: true, mode: 0o700 });
			stage = "execute review form";
			await page.setViewportSize({ width: 1440, height: 1000 });
			await page.goto(pack.index.formUrl);
			await page
				.getByLabel("Review Note")
				.fill("Synthetic design review submission");
			const submitted = page.waitForResponse(
				(response) =>
					new URL(response.url()).pathname ===
						`/api/forms/${pack.ids.formId}/submissions` &&
					response.request().method() === "POST",
			);
			await page
				.getByRole("button", { name: "Submit", exact: true })
				.click();
			const submission = await submitted;
			expect(submission.ok(), "submit debug review form").toBe(true);
			const { execution_id } = (await submission.json()) as {
				execution_id: string;
			};
			await page.waitForURL(
				(u) => u.pathname === `/history/${execution_id}`,
			);
			await page
				.getByRole("tab", { name: "Result", exact: true })
				.click();
			const result = page.getByRole("tabpanel", { name: "Result" });
			await expect(
				result.getByText(pack.index.expected.formResultMarker).first(),
			).toBeVisible();
			await expect(
				result.getByText(pack.index.expected.mappingEntity).first(),
			).toBeVisible();
			await page.screenshot({
				path: resolve(artifacts, "execution-desktop.png"),
			});
			await page.setViewportSize({ width: 390, height: 844 });
			await page.screenshot({
				path: resolve(artifacts, "execution-mobile.png"),
			});
			stage = "deliver local webhook";
			const marker = `${pack.names.eventMarker}-${Date.now()}`;
			const sent = await api.post(pack.index.webhookPath, {
				data: { ...pack.index.webhookPayload, marker },
			});
			expect(sent.status(), "accept debug review webhook").toBe(202);
			let eventExecutionId = "";
			await expect
				.poll(
					async () => {
						const response = await api.get(
							`/api/events/sources/${pack.ids.eventSourceId}/events`,
							{ params: { limit: 100 } },
						);
						expect(response.ok(), "read owned review events").toBe(
							true,
						);
						const events = (await response.json()) as {
							items: { id: string; data?: { marker?: string } }[];
						};
						const event = events.items.find(
							(item) => item.data?.marker === marker,
						);
						if (!event) return false;
						const deliveryResponse = await api.get(
							`/api/events/${event.id}/deliveries`,
						);
						expect(
							deliveryResponse.ok(),
							"read owned review deliveries",
						).toBe(true);
						const deliveries = (await deliveryResponse.json()) as {
							items: {
								status: string;
								execution_id: string | null;
							}[];
						};
						const delivery = deliveries.items.find(
							(item) =>
								item.status === "success" && item.execution_id,
						);
						eventExecutionId = delivery?.execution_id ?? "";
						return Boolean(eventExecutionId);
					},
					{ timeout: 30000 },
				)
				.toBe(true);
			stage = "review Home layouts";
			await page.goto("/");
			await expect(
				page.getByRole("heading", {
					name: "Workspace",
					exact: true,
					level: 1,
				}),
			).toBeVisible();
			await expect(
				page
					.getByRole("navigation", {
						name: "Collections",
						exact: true,
					})
					.getByText(pack.names.collection, { exact: true }),
			).toBeVisible();
			await page.screenshot({
				path: resolve(artifacts, "home-mobile.png"),
			});
			await page.setViewportSize({ width: 1440, height: 1000 });
			await page.screenshot({
				path: resolve(artifacts, "home-desktop.png"),
			});
			review = {
				formExecutionUrl: `/history/${execution_id}`,
				eventExecutionUrl: `/history/${eventExecutionId}`,
			};
		}
		console.log(
			JSON.stringify(
				{
					url,
					namespace: pack.namespace,
					names: pack.names,
					ids: pack.ids,
					index: pack.index,
					review,
				},
				null,
				2,
			),
		);
	} catch (error) {
		if (artifacts && page) {
			mkdirSync(artifacts, { recursive: true, mode: 0o700 });
			writeFileSync(
				resolve(artifacts, "review-failure.json"),
				JSON.stringify(
					{
						stage,
						path: new URL(page.url()).pathname,
						error:
							error instanceof Error
								? error.message.split("\n")[0]
								: "Unknown error",
						networkFailures,
					},
					null,
					2,
				),
				{ mode: 0o600 },
			);
		}
		if (
			artifacts &&
			page &&
			[
				"execute review form",
				"deliver local webhook",
				"review Home layouts",
			].includes(stage)
		) {
			mkdirSync(artifacts, { recursive: true, mode: 0o700 });
			await page.screenshot({
				path: resolve(artifacts, "review-failure.png"),
			});
		}
		// Playwright errors may include call arguments. Never print credentials or raw traces.
		console.error(
			`Review seeding failed during: ${stage}. Existing resources were preserved.`,
		);
		process.exitCode = 1;
	} finally {
		await browser.close();
	}
}

void main();
