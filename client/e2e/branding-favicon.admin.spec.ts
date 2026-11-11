import { test, expect } from "./fixtures/api-fixture";

// Seed through the existing upload endpoint before opening the page: this is
// the persisted-logo path used by an installation upgrading with a saved logo.
test("uses the previously saved square logo on platform and login pages", async ({
	api,
	page,
	browser,
}) => {
	const saved = await api.get("/api/branding/logo/square");
	expect([200, 404]).toContain(saved.status());
	const savedBytes = saved.ok() ? await saved.body() : null;
	const savedType = saved.headers()["content-type"] ?? "image/svg+xml";
	const logo = Buffer.from(
		'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="#336699"/></svg>',
	);
	const guest = await browser.newContext();
	try {
		const upload = await api.post("/api/branding/logo/square", {
			multipart: {
				file: {
					name: "square.svg",
					mimeType: "image/svg+xml",
					buffer: logo,
				},
			},
		});
		expect(upload.ok(), await upload.text()).toBe(true);
		await page.goto("/settings/branding");
		const icon = page.locator('link[rel="icon"]');
		await expect(icon).toHaveAttribute(
			"href",
			/\/api\/branding\/logo\/square\?v=\d+/,
		);
		const image = await page.request.get(
			(await icon.getAttribute("href")) as string,
		);
		expect(image.ok()).toBe(true);
		expect(await image.text()).toContain("#336699");

		const login = await guest.newPage();
		await login.goto(new URL("/login", page.url()).href);
		await expect(login.locator('link[rel="icon"]')).toHaveAttribute(
			"href",
			/\/api\/branding\/logo\/square\?v=\d+/,
		);
	} finally {
		await guest.close();
		const restore = savedBytes
			? await api.post("/api/branding/logo/square", {
					multipart: {
						file: {
							name: "original-logo",
							mimeType: savedType,
							buffer: savedBytes,
						},
					},
				})
			: await api.delete("/api/branding/logo/square");
		expect(restore.ok(), await restore.text()).toBe(true);
	}
});
