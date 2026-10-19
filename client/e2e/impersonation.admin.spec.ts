import { test, expect, grantWorkspaceAppPolicy, publishAppAndWait } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";
import { readFileSync } from "node:fs";
import { getCredentialsPath, type UserCredentials } from "./fixtures/users";
import { loginUserViaAPI } from "./setup/auth-helpers";

// One publish job proves the session crosses the real app bundler/host boundary.
// Permission and lifecycle edge cases live in the backend/unit tests.
test("impersonates every regular tab, reloads apps, and exits everywhere", async ({ page, context, api }) => {
	const suffix = crypto.randomUUID().slice(0, 8);
	const email = `impersonation-${suffix}@example.com`;
	const slug = `impersonation-${suffix}`;
	let orgId: string | undefined;
	let userId: string | undefined;
	let appId: string | undefined;
	let operatorToken: string | undefined;
	const operatorTab = await context.newPage();
	let newTab: typeof page | undefined;
	try {
		// Keep first-login announcements out of the support-session journey.
		const feed = await api.get("/api/product-updates");
		expect(feed.ok(), await feed.text()).toBe(true);
		const updates = await feed.json() as components["schemas"]["ProductUpdatesFeedResponse"];
		const receipt = await api.post("/api/product-updates/receipts", { data: { entry_ids: updates.bundle.entries.filter(entry => entry.in_app !== false).map(entry => entry.id) } });
		expect(receipt.ok(), await receipt.text()).toBe(true);
		const org = await api.post("/api/organizations", { data: { name: `Impersonation ${suffix}` } });
		expect(org.ok(), await org.text()).toBe(true);
		orgId = (await org.json()).id;
		const person = await api.post("/api/users", { data: { email, name: "Impersonation Demo", organization_id: orgId, password: "Synthetic-Demo-Only-473!", is_active: true } });
		expect(person.ok(), await person.text()).toBe(true);
		userId = (await person.json()).id;
		// A global app is visible both before impersonation and after Exit;
		// the target still uses its own organization and ordinary user identity.
		const app = await api.post("/api/applications", { data: { name: "Impersonation App", slug, organization_id: null, app_model: "inline_v1", access_level: "authenticated" } });
		expect(app.ok(), await app.text()).toBe(true);
		appId = (await app.json()).id;
		await grantWorkspaceAppPolicy(api, slug);
		for (const [path, content] of [
			[`apps/${slug}/_layout.tsx`, 'import { Outlet } from "react-router-dom"; export default function Layout() { return <Outlet />; }'],
			[`apps/${slug}/pages/index.tsx`, 'import { useUser } from "bifrost"; export default function Home() { const user = useUser(); return <section><h1>Impersonation App</h1><p>Signed in as {user.email}</p></section>; }'],
		]) {
			const written = await api.post("/api/files/write", { data: { path, content: Buffer.from(content).toString("base64"), mode: "cloud", location: "workspace", binary: true } });
			expect(written.ok(), await written.text()).toBe(true);
		}
		await publishAppAndWait(api, appId!);
		await page.goto(`/users/${userId}/profile`);
		// Exit rotates its refresh cookie. Give this test its own login so it
		// cannot revoke the shared setup session used by other browser tests.
		const credentials = JSON.parse(readFileSync(new URL(`./${getCredentialsPath()}`, import.meta.url), "utf8")) as Record<string, UserCredentials>;
		const login = await loginUserViaAPI(credentials.platform_admin);
		operatorToken = login.accessToken;
		await context.addCookies([
			{ name: "access_token", value: login.accessToken, url: new URL(page.url()).origin, httpOnly: true },
			{ name: "refresh_token", value: login.refreshToken, url: new URL(page.url()).origin, httpOnly: true },
		]);
		await page.evaluate(token => localStorage.setItem("bifrost_access_token", token), login.accessToken);
		const originalToken = await page.evaluate(() => localStorage.getItem("bifrost_access_token"));
		const operatorEmail = await page.evaluate(() => {
			const token = localStorage.getItem("bifrost_access_token");
			if (!token) throw new Error("Missing operator session");
			return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email as string;
		});
		await operatorTab.goto(`/apps/${slug}`);
		await expect(operatorTab.getByRole("heading", { name: "Impersonation App" })).toBeVisible();
		await expect(operatorTab.getByText(`Signed in as ${operatorEmail}`, { exact: true })).toBeVisible();
		await page.getByRole("button", { name: "Impersonate", exact: true }).click();
		await expect(page).toHaveURL(/\/apps$/);
		await expect(page.getByRole("status").filter({ hasText: "Impersonating" })).toContainText(email);
		await expect(operatorTab.getByRole("status").filter({ hasText: "Impersonating" })).toContainText(email);
		await expect(operatorTab.getByText(`Signed in as ${email}`, { exact: true })).toBeVisible();
		newTab = await context.newPage();
		await newTab.goto(`/apps/${slug}`);
		await expect(newTab.getByRole("status").filter({ hasText: "Impersonating" })).toContainText(email);
		await expect(newTab.getByText(`Signed in as ${email}`, { exact: true })).toBeVisible();
		await page.goto(`/apps/${slug}`);
		await expect(page.getByRole("heading", { name: "Impersonation App" })).toBeVisible();
		await expect(page.getByText(`Signed in as ${email}`, { exact: true })).toBeVisible();
		await page.reload();
		await expect(page.getByText(`Signed in as ${email}`, { exact: true })).toBeVisible();
		expect(await page.evaluate(() => localStorage.getItem("bifrost_access_token"))).toBe(originalToken);
		// Exercise recovery through the original refresh cookie, then verify
		// every tab resumes with one fresh operator token rather than racing.
		await page.evaluate(() => localStorage.setItem("bifrost_access_token", "expired-operator-access"));
		await page.getByRole("button", { name: "Exit", exact: true }).click();
		await expect(page).toHaveURL(/\/users/);
		await expect(page.getByRole("button", { name: "Exit", exact: true })).toHaveCount(0);
		await expect(operatorTab.getByRole("button", { name: "Exit", exact: true })).toHaveCount(0);
		await expect(newTab.getByRole("button", { name: "Exit", exact: true })).toHaveCount(0);
		await expect(operatorTab.getByText(`Signed in as ${operatorEmail}`, { exact: true })).toBeVisible();
		await expect(newTab.getByText(`Signed in as ${operatorEmail}`, { exact: true })).toBeVisible();
		expect(await page.evaluate(() => localStorage.getItem("bifrost_access_token"))).not.toBe("expired-operator-access");
	} finally {
		await newTab?.close();
		await operatorTab.close();
		if (operatorToken) await context.request.post("/api/auth/logout", { headers: { Authorization: `Bearer ${operatorToken}` } });
		if (appId) await api.delete(`/api/applications/${appId}`);
		if (userId) await api.delete(`/api/users/${userId}`);
		if (orgId) await api.delete(`/api/organizations/${orgId}`);
	}
});
