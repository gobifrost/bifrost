import { test, expect } from "@playwright/test";

type GitHubConfig = {
	configured: boolean;
	token_saved: boolean;
	repo_url: string | null;
	branch: string | null;
	backup_path: string | null;
};

const REPO = "fixture-owner/settings-acceptance";
const CREATED_REPO = "fixture-owner/settings-created";
const BRANCH = "release/settings-acceptance";

function unconfiguredConfig(): GitHubConfig {
	return {
		configured: false,
		token_saved: false,
		repo_url: null,
		branch: null,
		backup_path: null,
	};
}

test.describe("GitHub settings acceptance (admin)", () => {
	test("reviews a selected repository and queues an explicit reconciliation", async ({
		page,
	}) => {
		const config = unconfiguredConfig();
		let validateCalls = 0;
		let previewPayload: unknown;
		let connectPayload: unknown;
		const repositories = [
			{ full_name: REPO, private: true },
			{ full_name: "fixture-owner/secondary", private: false },
		];

		await page.route("**/api/github/config", async (route) => {
			await route.fulfill({ json: config });
		});
		await page.route("**/api/github/validate", async (route) => {
			validateCalls += 1;
			await route.fulfill({
				json: {
					repositories,
					detected_repo: null,
				},
			});
		});
		await page.route("**/api/github/branches?**", async (route) => {
			const url = new URL(route.request().url());
			const repo = url.searchParams.get("repo");
			expect([REPO, CREATED_REPO]).toContain(repo);
			await route.fulfill({
				json: {
					branches:
						repo === CREATED_REPO
							? [{ name: "main", protected: false }]
							: [
									{ name: "main", protected: true },
									{ name: BRANCH, protected: false },
								],
				},
			});
		});
		await page.route("**/api/github/create-repository", async (route) => {
			const payload = route.request().postDataJSON() as {
				name: string;
				description: string | null;
				private: boolean;
				organization: string | null;
			};
			expect(payload).toMatchObject({
				name: "settings-created",
				description: "Created from settings acceptance",
				private: true,
				organization: null,
			});
			await route.fulfill({
				json: {
					full_name: CREATED_REPO,
					private: true,
				},
			});
		});
		await page.route("**/api/github/connect/preview", async (route) => {
			previewPayload = route.request().postDataJSON();
			await route.fulfill({
				json: {
					token: "review-token",
					repository_url: `https://github.com/${REPO}`,
					branch: BRANCH,
					state: "requires_reconciliation",
					items: [
						{
							path: "apps/local.tsx",
							classification: "local_only",
						},
						{
							path: "apps/remote.tsx",
							classification: "remote_only",
						},
						{ path: "apps/shared.tsx", classification: "conflict" },
					],
				},
			});
		});
		await page.route("**/api/github/connect", async (route) => {
			connectPayload = route.request().postDataJSON();
			await route.fulfill({
				status: 202,
				json: {
					job_id: "github-settings-job",
					status: "queued",
					notification_id: "github-settings-notification",
				},
			});
		});

		await page.goto("/settings/github");
		await expect(
			page.getByText("GitHub Integration", { exact: true }),
		).toBeVisible();

		await page
			.getByLabel("GitHub Personal Access Token")
			.fill("ghp_fixture_token");
		await page.getByRole("button", { name: "Validate" }).click();
		await expect(
			page.getByRole("status").filter({ hasText: "Token validated." }),
		).toBeVisible();
		expect(validateCalls).toBe(1);

		await page.getByRole("combobox", { name: /repository/i }).click();
		await page
			.getByRole("option", {
				name: /fixture-owner\/settings-acceptance/i,
			})
			.click();
		await page.getByRole("combobox", { name: /branch/i }).click();
		await page
			.getByRole("option", { name: /release\/settings-acceptance/i })
			.click();

		await page.getByRole("button", { name: "Create New" }).click();
		await page.getByLabel("Repository Name").fill("settings-created");
		await page
			.getByLabel("Description (Optional)")
			.fill("Created from settings acceptance");
		await page.getByRole("button", { name: "Create Repository" }).click();
		await expect(page.getByRole("dialog")).not.toBeVisible();

		await page.getByRole("combobox", { name: /repository/i }).click();
		await page
			.getByRole("option", {
				name: /fixture-owner\/settings-acceptance/i,
			})
			.click();
		await page.getByRole("combobox", { name: /branch/i }).click();
		await page
			.getByRole("option", { name: /release\/settings-acceptance/i })
			.click();
		await page.getByRole("button", { name: "Review connection" }).click();

		expect(previewPayload).toEqual({
			repository_url: REPO,
			branch: BRANCH,
		});
		const review = page.getByRole("region", {
			name: "Review workspace connection",
		});
		await expect(review).toBeVisible();
		await review.getByRole("radio", { name: /reconcile both/i }).click();
		await review
			.getByRole("radio", { name: /keep local: apps\/shared.tsx/i })
			.click();
		await review.getByRole("button", { name: "Connect GitHub" }).click();

		expect(connectPayload).toEqual({
			preview_token: "review-token",
			strategy: "reconcile",
			decisions: { "apps/shared.tsx": "local" },
			confirm_destructive: false,
		});
		await expect(
			page
				.getByRole("status", { name: "" })
				.filter({ hasText: "Connection queued" }),
		).toBeVisible();
	});
});
