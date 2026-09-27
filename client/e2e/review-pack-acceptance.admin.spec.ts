import { expect, test, type AuthedApi } from "./fixtures/api-fixture";
import { ensureReviewPack, type ReviewPack } from "./fixtures/review-pack";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;

type EventRecord = {
	id: string;
	event_type: string | null;
	status: string;
	data?: Record<string, unknown>;
	success_count: number;
	delivery_count: number;
};

type DeliveryRecord = {
	id: string | null;
	status: string;
	workflow_name: string | null;
	execution_id: string | null;
};

async function expectOk(response: { ok(): boolean; text(): Promise<string> }) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function countOwnedSubscriptions(api: AuthedApi, pack: ReviewPack) {
	const response = await api.get(
		`/api/events/sources/${pack.ids.eventSourceId}/subscriptions`,
	);
	await expectOk(response);
	const body = (await response.json()) as {
		items: Array<{ workflow_id: string | null; event_type: string | null }>;
	};
	return body.items.filter(
		(subscription) =>
			subscription.workflow_id === pack.ids.eventWorkflowId &&
			subscription.event_type === pack.names.eventType,
	).length;
}

async function countOwnedCollections(api: AuthedApi, pack: ReviewPack) {
	const response = await api.get("/api/home");
	await expectOk(response);
	const body = (await response.json()) as {
		collections: Array<{ id: string; name: string }>;
	};
	return body.collections.filter(
		(collection) => collection.name === pack.names.collection,
	).length;
}

async function sendWebhook(api: AuthedApi, pack: ReviewPack) {
	const response = await api.post(pack.index.webhookPath, {
		data: pack.index.webhookPayload,
		headers: { "Content-Type": "application/json" },
	});
	expect(response.status(), await response.text()).toBe(202);
}

async function getOwnedEvent(api: AuthedApi, pack: ReviewPack) {
	const response = await api.get(
		`/api/events/sources/${pack.ids.eventSourceId}/events`,
		{ params: { event_type: pack.names.eventType, limit: 100 } },
	);
	await expectOk(response);
	const body = (await response.json()) as { items: EventRecord[] };
	return body.items.find(
		(event) =>
			event.event_type === pack.names.eventType &&
			event.data?.marker === pack.names.eventMarker,
	);
}

async function getDeliveries(api: AuthedApi, eventId: string) {
	const response = await api.get(`/api/events/${eventId}/deliveries`);
	await expectOk(response);
	const body = (await response.json()) as { items: DeliveryRecord[] };
	return body.items;
}

async function waitForSuccessfulDelivery(api: AuthedApi, pack: ReviewPack) {
	const startedAt = Date.now();
	let lastStatus = "no event";

	for (;;) {
		const event = await getOwnedEvent(api, pack);
		if (event) {
			const deliveries = await getDeliveries(api, event.id);
			const delivery = deliveries.find(
				(candidate) =>
					candidate.workflow_name === pack.names.eventWorkflow,
			);
			if (delivery) {
				lastStatus = `${event.status}/${delivery.status}`;
				if (delivery.status === "success" && delivery.execution_id) {
					return { event, delivery };
				}
			} else {
				lastStatus = `${event.status}/no matching delivery`;
			}
		}

		if (Date.now() - startedAt > 60_000) {
			throw new Error(
				`Event ${pack.names.eventType} did not reach successful delivery; last status ${lastStatus}`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
}

test.describe("review fixture pack acceptance", () => {
	test("ensures an idempotent review namespace and drives seeded Home and webhook journeys", async ({
		page,
		api,
		context,
	}) => {
		const namespace = `acceptance-${UNIQUE}`;
		const first = await ensureReviewPack(api, namespace);
		const second = await ensureReviewPack(api, namespace);

		try {
			expect(second.ids).toEqual(first.ids);
			expect(await countOwnedSubscriptions(api, first)).toBe(1);
			expect(await countOwnedCollections(api, first)).toBe(1);

			await page.goto(first.index.homeUrl);
			await expect(
				page.getByRole("heading", {
					name: "Workspace",
					exact: true,
					level: 1,
				}),
			).toBeVisible({ timeout: 10_000 });
			await expect(
				page.getByRole("region", { name: "Browse resources" }),
			).toBeVisible();
			await page
				.getByRole("textbox", { name: "Search Home resources" })
				.fill(first.names.form);
			await page
				.getByRole("button", { name: first.names.form, exact: true })
				.click();
			await expect(page).toHaveURL(
				new RegExp(`/execute/${first.ids.formId}$`),
			);

			const reviewNote = `review pack note ${UNIQUE}`;
			await expect(
				page.getByRole("heading", { name: first.names.form }),
			).toBeVisible();
			await page.getByLabel("Review Note").fill(reviewNote);
			const submission = page.waitForResponse(
				(response) =>
					response
						.url()
						.includes(
							`/api/forms/${first.ids.formId}/submissions`,
						) && response.request().method() === "POST",
			);
			await page.getByRole("button", { name: "Submit" }).click();
			const submissionResponse = await submission;
			await expectOk(submissionResponse);
			const submissionBody = (await submissionResponse.json()) as {
				execution_id: string;
			};
			expect(submissionBody.execution_id).toMatch(/^[0-9a-f-]{36}$/i);

			await expect(page).toHaveURL(
				new RegExp(`/history/${submissionBody.execution_id}$`),
				{ timeout: 10_000 },
			);
			await page.getByRole("tab", { name: "Result" }).click();
			const formResultPanel = page.getByRole("tabpanel", {
				name: "Result",
			});
			await expect(formResultPanel).toBeVisible({ timeout: 30_000 });
			await expect(
				formResultPanel
					.getByText(first.index.expected.formResultMarker)
					.first(),
			).toBeVisible({ timeout: 30_000 });
			await expect(
				formResultPanel
					.getByText(first.index.expected.mappingEntity)
					.first(),
			).toBeVisible();
			await expect(
				formResultPanel.getByText(reviewNote).first(),
			).toBeVisible();

			await sendWebhook(api, first);
			const { delivery } = await waitForSuccessfulDelivery(api, first);
			await page.goto(first.index.eventSourceUrl);
			await expect(
				page.getByRole("heading", {
					level: 1,
					name: first.names.eventSource,
				}),
			).toBeVisible({ timeout: 10_000 });
			await page.getByRole("tab", { name: "Events" }).click();
			await page.getByLabel("Search events").fill(first.names.eventType);
			const eventRow = page.getByRole("row", {
				name: new RegExp(first.names.eventType),
			});
			await expect(eventRow).toBeVisible({ timeout: 10_000 });
			await expect(eventRow).toContainText("Completed");
			await expect(eventRow).toContainText("1 ok");
			await eventRow
				.getByRole("link", { name: first.names.eventType })
				.click();

			const eventDialog = page.getByRole("dialog", {
				name: "Event Details",
			});
			await expect(eventDialog).toBeVisible({ timeout: 10_000 });
			await expect(eventDialog).toContainText(first.names.eventMarker);
			await expect(eventDialog).toContainText(first.names.eventWorkflow);
			await expect(eventDialog).toContainText("Success");

			const executionPagePromise = context.waitForEvent("page");
			await eventDialog
				.getByRole("link", { name: /view execution/i })
				.click();
			const executionPage = await executionPagePromise;
			await executionPage.waitForLoadState("domcontentloaded");
			await expect(executionPage).toHaveURL(
				new RegExp(`/history/${delivery.execution_id}$`),
			);
			await executionPage.getByRole("tab", { name: "Result" }).click();
			const eventResultPanel = executionPage.getByRole("tabpanel", {
				name: "Result",
			});
			await expect(eventResultPanel).toBeVisible({ timeout: 30_000 });
			await expect(
				eventResultPanel
					.getByText(first.index.expected.eventResultMarker)
					.first(),
			).toBeVisible({ timeout: 30_000 });
			await expect(
				eventResultPanel
					.getByText(first.index.expected.mappingEntity)
					.first(),
			).toBeVisible();
			await expect(
				eventResultPanel.getByText(first.names.eventMarker).first(),
			).toBeVisible();
		} finally {
			await first.cleanup();
		}
	});
});
