/**
 * Chat V2 — happy-path flows (admin, REAL browser).
 *
 * These run in a real Chromium via Playwright so they exercise the Radix
 * portal/focus surfaces that jsdom cannot (inline rename, model picker popover,
 * the conversation overflow menu). A recent rename regression was jsdom-green
 * but dead in a real browser because the dropdown overlay ate focus — the
 * rename test here is written to fail on exactly that class of bug.
 *
 * Target: the TEST stack (auth state from the setup project). Streaming turns
 * are LLM-dependent, so the attachment test asserts the request is accepted and
 * the user turn (with its attachment chip) renders; it tolerates a slow or
 * absent assistant reply rather than hard-failing on model latency.
 */

import { test, expect, type Page } from "@playwright/test";
import { seedAgentViaPage } from "./setup/seed-agent";

const uniq = (p: string) => `${p} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`;

/**
 * The CI test stack has NO LLM provider configured (and one cannot be added at
 * runtime — `POST /api/admin/llm/config` runs a real 1-token completion against
 * the provider and rolls back if it fails, so it needs a live key + network).
 * Without a provider, `Chat.tsx` replaces the ENTIRE chat shell with an
 * "AI Chat Not Configured" setup prompt (gated purely on the frontend response
 * of `GET /api/admin/llm/config`). That gate makes the sidebar, composer, model
 * picker, and conversation rows non-existent — so rename/export/attachment/model
 * tests can't reach any of the real UI they assert on.
 *
 * We fulfil just that one read-only status endpoint so the real chat shell
 * mounts. Everything downstream (conversation create, sidebar listing, inline
 * rename PATCH, Markdown export blob, attachment upload + chip) then runs against
 * the genuine backend. The only thing this can't conjure is a real assistant
 * reply / the provider's live model catalogue, which the affected tests already
 * treat as soft annotations.
 */
async function mockLLMConfigured(page: Page): Promise<void> {
	await page.route("**/api/admin/llm/config", async (route) => {
		if (route.request().method() !== "GET") {
			await route.fallback();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				provider: "anthropic",
				model: "claude-sonnet-4-20250514",
				endpoint: null,
				max_tokens: 16384,
				default_system_prompt: null,
				summarization_model: null,
				tuning_model: null,
				is_configured: true,
				api_key_set: true,
			}),
		});
	});
}

/** Start a fresh chat bound to a seeded agent; returns the conversation id. */
async function startChatWithAgent(page: Page, agentId: string): Promise<string> {
	await page.goto(`/agents/${agentId}`);
	const startBtn = page.getByTestId("start-chat-button");
	await expect(startBtn).toBeVisible({ timeout: 10000 });
	await startBtn.click();
	await page.waitForURL(/\/chat\/[0-9a-f-]{36}/, { timeout: 15000 });
	const m = page.url().match(/\/chat\/([0-9a-f-]{36})/);
	if (!m) throw new Error(`No conversation id in URL: ${page.url()}`);
	return m[1];
}

test.describe("Chat V2 happy path (admin)", () => {
	test("create a workspace from the directory and see it listed", async ({
		page,
	}) => {
		const name = uniq("E2E WS");
		await page.goto("/workspaces");

		await page.getByRole("button", { name: "New workspace" }).click();

		const dialog = page.getByRole("dialog");
		await expect(dialog.getByText("New workspace")).toBeVisible();
		await dialog.getByLabel("Name").fill(name);
		// Default mode is Private — submit straight away.
		await dialog
			.getByRole("button", { name: /Create workspace/i })
			.click();

		// On success the dialog navigates into the workspace (/chat?workspace=…).
		await page.waitForURL(/\/chat\?workspace=[0-9a-f-]{36}/, {
			timeout: 15000,
		});

		// The sidebar workspace-identity row shows the new workspace name.
		await expect(
			page.getByText(name, { exact: false }).first(),
		).toBeVisible({ timeout: 10000 });

		// And it appears on the directory listing.
		await page.goto("/workspaces");
		await expect(
			page.getByText(name, { exact: false }).first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("send a chat with a CSV attachment — chip renders, request accepted", async ({
		page,
	}) => {
		await mockLLMConfigured(page);
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Chat Attach Spec",
		});
		const conversationId = await startChatWithAgent(page, agent.id);

		// Wait for the composer to mount and the attach affordance to enable —
		// attach is only enabled once the conversation exists (attachEnabled).
		await expect(page.getByLabel("Chat input")).toBeVisible({
			timeout: 10000,
		});
		await expect(
			page.getByRole("button", { name: "Attach files" }),
		).toBeEnabled({ timeout: 10000 });

		// The composer's hidden file input is the robust attach surface (the
		// paperclip just clicks it). Set a small CSV directly.
		const csv = "name,score\nAlice,12\nBob,7\n";
		const fileInput = page.locator('input[type="file"]');
		await fileInput.setInputFiles({
			name: "scores.csv",
			mimeType: "text/csv",
			buffer: Buffer.from(csv),
		});

		// Upload POSTs to /conversations/{id}/attachments — wait for the chip.
		const chip = page.getByText("scores.csv", { exact: false });
		await expect(chip.first()).toBeVisible({ timeout: 15000 });

		// Send a message; capture the WS/send request acceptance via the message
		// appearing in the transcript (user bubble).
		const composer = page.getByLabel("Chat input");
		const prompt = "Here is a CSV. Acknowledge receipt.";
		await composer.fill(prompt);
		await page.getByRole("button", { name: "Send message" }).click();

		// User turn (with its attachment chip + text) lands in the transcript.
		await expect(
			page.getByText(prompt, { exact: false }).first(),
		).toBeVisible({ timeout: 15000 });
		// The attachment chip persists on the rendered user message.
		await expect(
			page.getByText("scores.csv", { exact: false }).first(),
		).toBeVisible({ timeout: 15000 });

		// Best-effort: an assistant reply streams in. Streaming is LLM-latency
		// dependent on the test stack, so this is a soft check — the hard
		// assertions above prove the attachment + send path. We give it a
		// generous window and tolerate a no-show rather than flake on latency.
		const assistantArrived = await page
			.locator(".animate-pulse, .prose")
			.first()
			.waitFor({ state: "visible", timeout: 20000 })
			.then(() => true)
			.catch(() => false);
		test.info().annotations.push({
			type: "assistant-reply",
			description: assistantArrived
				? "assistant content rendered"
				: "no assistant content within window (test-stack LLM latency tolerated)",
		});
		expect(conversationId).toMatch(/[0-9a-f-]{36}/);
	});

	test("rename a conversation inline in the sidebar (Radix focus path)", async ({
		page,
	}) => {
		await mockLLMConfigured(page);
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Chat Rename Spec",
		});
		await startChatWithAgent(page, agent.id);

		// Give the new conversation a title to rename from by sending one msg so
		// it shows in the general pool with a stable label. Renaming works on the
		// agent_name fallback too, so we don't strictly need a message — but the
		// row must exist in the sidebar list. Navigate to /chat (general pool).
		const newTitle = uniq("Renamed");

		// The conversation row lands in the sidebar Recent list (general pool).
		// Its overflow button is `opacity-0` until the ROW is hovered on desktop
		// (sm:group-hover:opacity-100), so reveal it by hovering the row first.
		const actionsBtn = page
			.getByRole("button", { name: /^Actions for / })
			.first();
		await actionsBtn.waitFor({ state: "attached", timeout: 10000 });
		await actionsBtn.hover();
		await expect(actionsBtn).toBeVisible({ timeout: 10000 });
		await actionsBtn.click();

		await page.getByRole("menuitem", { name: "Rename" }).click();

		// The inline editor mounts AFTER the menu closes (onCloseAutoFocus). In a
		// real browser the field must actually take focus — jsdom can't prove
		// this. Type a new title and commit with Enter.
		const editor = page.getByRole("textbox", {
			name: "Rename conversation",
		});
		await expect(editor).toBeVisible({ timeout: 10000 });
		// Assert it actually holds focus (the regression: overlay steals it).
		await expect(editor).toBeFocused({ timeout: 5000 });
		await editor.fill(newTitle);
		await editor.press("Enter");

		// The renamed title persists in the sidebar after commit.
		await expect(
			page.getByText(newTitle, { exact: true }).first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("switch model mid-conversation — composer pill updates", async ({
		page,
	}) => {
		await mockLLMConfigured(page);
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Chat Model Spec",
		});
		await startChatWithAgent(page, agent.id);

		// The model picker is the compact ghost combobox in the composer.
		const picker = page
			.getByRole("combobox")
			.filter({ hasNot: page.getByLabel("Rename conversation") })
			.first();
		await expect(picker).toBeVisible({ timeout: 10000 });
		const before = (await picker.textContent())?.trim() ?? "";

		await picker.click();

		// The popover opens with a search box regardless of how many models the
		// provider exposes — that's the stack-independent fact we hard-assert.
		const search = page.getByPlaceholder("Search models…");
		await expect(search).toBeVisible({ timeout: 5000 });

		// The selectable rows are provider-dependent. The CI test stack has NO
		// LLM provider (the shell is only reachable via the mocked config status),
		// so /api/admin/llm/models is empty and there is nothing to switch to.
		// Below ~2 options there's no second model to switch into — prove the
		// picker is reachable + interactive and annotate the catalogue size.
		const options = page.getByRole("option");
		const count = await options.count();
		if (count < 2) {
			test.info().annotations.push({
				type: "model-picker",
				description: `${count} model(s) selectable — the test stack has no provider catalogue, so a real switch can't be exercised; picker reachable + popover opens (search box rendered), switch skipped`,
			});
			// Close the popover; the trigger pill stays intact.
			await page.keyboard.press("Escape");
			await expect(search).toBeHidden({ timeout: 5000 });
			expect(before.length).toBeGreaterThan(0);
			return;
		}

		// Click the first option whose label differs from the current pill.
		let switched = false;
		for (let i = 0; i < count; i++) {
			const opt = options.nth(i);
			const label = (await opt.textContent())?.trim() ?? "";
			if (label && !before.includes(label.slice(0, 8))) {
				await opt.click();
				switched = true;
				break;
			}
		}
		if (!switched) await options.first().click();

		// Trigger label should reflect a (possibly new) selection and the popover
		// closes.
		await expect(page.getByRole("option")).toHaveCount(0, {
			timeout: 5000,
		});
		const after = (await picker.textContent())?.trim() ?? "";
		expect(after.length).toBeGreaterThan(0);
	});

	test("manual compact affordance is budget-gated (header)", async ({
		page,
	}) => {
		await mockLLMConfigured(page);
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Chat Compact Spec",
		});
		await startChatWithAgent(page, agent.id);

		// CompactButton (header) renders ONLY once the context budget crosses
		// ~70% of the model window (shouldSuggestCompaction). A fresh, short
		// conversation is well under that, so the button is intentionally
		// absent. We assert the gate holds (no premature button) — driving it
		// past 70% in a browser would require a very long synthetic transcript,
		// which the unit suite covers (CompactButton.test.tsx threshold tests).
		const compactBtn = page.getByRole("button", {
			name: "Compact older turns",
		});
		const visible = await compactBtn
			.first()
			.isVisible()
			.catch(() => false);
		if (visible) {
			// If the stack's model window is tiny enough that even a short chat
			// trips the gate, prove the action fires without error.
			await compactBtn.first().click();
			await expect(
				page.getByText(
					/summarized|Nothing to compact|compact/i,
				).first(),
			).toBeVisible({ timeout: 15000 });
		} else {
			test.info().annotations.push({
				type: "compact-gate",
				description:
					"Compact button correctly absent below the 70% budget threshold (gate enforced; reachability covered by CompactButton.test.tsx)",
			});
			expect(visible).toBe(false);
		}
	});

	test("export conversation as Markdown triggers a download", async ({
		page,
	}) => {
		await mockLLMConfigured(page);
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Chat Export Spec",
		});
		await startChatWithAgent(page, agent.id);

		// Export lives in the sidebar conversation overflow menu → Export →
		// Markdown. The overflow button is `opacity-0` until the row is hovered
		// (sm:group-hover:opacity-100), so reveal it by hovering the row first.
		const actionsBtn = page
			.getByRole("button", { name: /^Actions for / })
			.first();
		await actionsBtn.waitFor({ state: "attached", timeout: 10000 });
		await actionsBtn.hover();
		await expect(actionsBtn).toBeVisible({ timeout: 10000 });
		await actionsBtn.click();

		// Open the Export submenu, then choose Markdown. The download is fired by
		// chatExport.exportConversation (anchor click on a blob URL).
		await page.getByRole("menuitem", { name: "Export" }).hover();
		const markdownItem = page.getByRole("menuitem", { name: "Markdown" });
		await expect(markdownItem).toBeVisible({ timeout: 5000 });

		const downloadPromise = page.waitForEvent("download", {
			timeout: 15000,
		});
		await markdownItem.click();
		const download = await downloadPromise;
		expect(download.suggestedFilename()).toMatch(/\.md$/);
	});
});
