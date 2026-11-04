import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("form upload sends the storage headers returned by the API", async ({
	page,
	api,
}) => {
	// Storage-only happy path: no workflow execution, build, or platform job.
	const name = `Storage upload fixture ${randomUUID()}`;
	const created = await api.post("/api/forms", {
		data: {
			name,
			workflow_id: null,
			access_level: "authenticated",
			form_schema: {
				fields: [
					{
						name: "document",
						label: "Document",
						type: "file",
						required: false,
					},
				],
			},
		},
	});
	expect(created.ok(), await created.text()).toBe(true);
	const formId = ((await created.json()) as { id: string }).id;
	let upload:
		| {
				upload_url: string;
				blob_uri: string;
				headers: Record<string, string>;
		  }
		| undefined;
	let uploadScope: string | undefined;
	try {
		await page.goto(`/execute/${formId}`);
		await expect(
			page.getByRole("heading", { name, exact: true }),
		).toBeVisible();
		const signing = page.waitForResponse(
			(response) =>
				response.url().endsWith(`/api/forms/${formId}/upload`) &&
				response.request().method() === "POST",
		);
		const putting = page.waitForResponse(
			(response) =>
				response.request().method() === "PUT" &&
				response.url().includes("/uploads/"),
		);
		await page
			.getByRole("group", { name: "Document", exact: true })
			.getByLabel("Document", { exact: true })
			.setInputFiles({
				name: "fixture-upload.txt",
				mimeType: "text/plain",
				buffer: Buffer.from("fixture upload"),
			});
		const signed = await signing;
		expect(signed.ok(), await signed.text()).toBe(true);
		upload = await signed.json();
		uploadScope = decodeURIComponent(
			new URL(upload!.upload_url, page.url()).pathname
				.split("/uploads/")[1]
				?.split("/")[0] ?? "",
		);
		expect(uploadScope).not.toBe("");
		expect(upload!.headers["Content-Type"]).toBe("text/plain");
		const put = await putting;
		expect([200, 201, 204]).toContain(put.status());
		const sent = await put.request().allHeaders();
		for (const [header, value] of Object.entries(upload!.headers)) {
			expect(sent[header.toLowerCase()]).toBe(value);
		}
		await expect(
			page.getByRole("button", {
				name: "Remove fixture-upload.txt",
				exact: true,
			}),
		).toBeVisible();
	} finally {
		try {
			if (upload && uploadScope) {
				const policyPath = `/api/files/policies/${encodeURIComponent(upload.blob_uri)}`;
				const params = { location: "uploads", scope: uploadScope };
				const grant = await api.put(policyPath, {
					params,
					data: {
						policies: {
							policies: [
								{
									name: "fixture_cleanup",
									actions: ["delete"],
									when: { user: "is_platform_admin" },
								},
							],
						},
					},
				});
				expect(grant.ok(), await grant.text()).toBe(true);
				try {
					const removed = await api.post("/api/files/delete", {
						data: { ...params, path: upload.blob_uri },
					});
					expect([204, 404], await removed.text()).toContain(
						removed.status(),
					);
				} finally {
					const removedPolicy = await api.delete(policyPath, {
						params,
					});
					expect([204, 404], await removedPolicy.text()).toContain(
						removedPolicy.status(),
					);
				}
			}
		} finally {
			const removed = await api.delete(`/api/forms/${formId}`);
			expect([200, 204, 404], await removed.text()).toContain(
				removed.status(),
			);
		}
	}
});
