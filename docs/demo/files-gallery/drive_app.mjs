import { chromium } from "/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies/client/node_modules/playwright-core/index.mjs";

const BASE = "http://localhost:34212";
const SHOT = "/tmp/files-drive";
const log = (...a) => console.log("[app]", ...a);

const browser = await chromium.launch();

async function asUser(email, label) {
	const ctx = await browser.newContext({ viewport: { width: 1100, height: 800 } });
	const page = await ctx.newPage();
	const errs = [];
	page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
	try {
		await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
		await page.fill('input[type="email"]', email);
		await page.fill('input[type="password"]', "password");
		await page.getByRole("button", { name: "Sign In", exact: true }).click();
		await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 });
		await page.goto(`${BASE}/apps/gallery`, { waitUntil: "networkidle" });
		await page.waitForTimeout(2500); // let useFiles load + render the grid
		await page.screenshot({ path: `${SHOT}/app-${label}.png`, fullPage: false });
		// read the rendered file names from the grid
		const names = await page.locator('[data-testid="grid"] > div').allInnerTexts().catch(() => []);
		log(label, email, "grid items:", JSON.stringify(names));
		if (errs.length) log(label, "errors:", JSON.stringify(errs));
	} catch (e) {
		log(label, "ERROR", e.message);
		await page.screenshot({ path: `${SHOT}/app-${label}-error.png` }).catch(() => {});
	} finally {
		await ctx.close();
	}
}

await asUser("dev@gobifrost.com", "admin");
await asUser("alice@gallery.example.com", "alice");
await asUser("bob@gallery.example.com", "bob");
await browser.close();
log("done");
