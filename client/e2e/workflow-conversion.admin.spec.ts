/**
 * Workflow conversion — platform-admin happy path.
 *
 * This uses a real synthetic workflow, durable PlatformJob execution, and
 * browser WebSocket progress. It deliberately does not mock conversion APIs.
 */
import { randomUUID } from "node:crypto";

import { test, expect, type AuthedApi } from "./fixtures/api-fixture";
import { buildSolutionZip } from "./fixtures/solution-zip";

const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
const workflowName = `synthetic_conversion_${suffix}`;
const workflowPath = `${workflowName}.py`;
const helperModule = `${workflowName}_helper`;
const helperPath = `${helperModule}.py`;

function solutionPermissionRequestZip(slug: string, workflowName: string) {
	const workflowId = randomUUID();
	return buildSolutionZip([
		{
			path: "bifrost.solution.yaml",
			content: JSON.stringify({
				slug,
				name: slug.toUpperCase(),
				global_repo_access: false,
			}),
		},
		{
			path: "workflows/solution_request.py",
			content: "async def run():\n    return None\n",
		},
		{
			path: ".bifrost/workflows.yaml",
			content: JSON.stringify({
				workflows: {
					[workflowId]: {
						id: workflowId,
						name: workflowName,
						function_name: "run",
						path: "workflows/solution_request.py",
						requested_permissions: {
							mode: "restricted",
							grants: [
								{ permission: "organizations.read" },
								{ permission: "configs.read" },
								{ permission: "integrations.read" },
								{ permission: "tables.read" },
							],
						},
					},
				},
			}),
		},
	]);
}

async function waitForDeploy(api: AuthedApi, jobId: string) {
	await expect
		.poll(
			async () => {
				const response = await api.get(
					`/api/solutions/deploy-jobs/${jobId}`,
				);
				expect(response.ok(), await response.text()).toBe(true);
				const status = (await response.json()) as {
					status: string;
					error?: string | null;
				};
				if (status.status === "failed") {
					throw new Error(status.error || "solution deploy failed");
				}
				return status.status;
			},
			{ timeout: 30_000 },
		)
		.toBe("succeeded");
}

test.describe("Workflow conversion", () => {
	test.beforeAll(async ({ api }) => {
		const helper = await api.put("/api/files/editor/content", {
			data: {
				path: helperPath,
				encoding: "utf-8",
				content:
					"from bifrost import organizations\n\nasync def load_organizations():\n    return await organizations.list()\n",
			},
		});
		expect(
			helper.ok(),
			`write helper fixture: ${await helper.text()}`,
		).toBe(true);
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: workflowPath,
				encoding: "utf-8",
				content: `from bifrost import workflow\nfrom ${helperModule} import load_organizations\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}() -> list:\n    return await load_organizations()\n`,
			},
		});
		expect(
			write.ok(),
			`write conversion fixture: ${await write.text()}`,
		).toBe(true);
		const register = await api.post("/api/workflows/register", {
			data: { path: workflowPath, function_name: workflowName },
		});
		expect(
			register.ok(),
			`register conversion fixture: ${await register.text()}`,
		).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		for (const path of [workflowPath, helperPath]) {
			const response = await api.delete(
				`/api/files/editor?path=${encodeURIComponent(path)}`,
			);
			expect([200, 204, 404]).toContain(response.status());
		}
	});

	test("WORKFLOW-CONVERSION-01 analyzes, reviews, applies and reopens a synthetic workflow", async ({
		page,
	}, testInfo) => {
		// Analyze and apply are two durable PlatformJob round trips; together
		// they take about 19s in CI. The global 30s ceiling leaves no headroom
		// for queueing, so this flow gets a measured budget, not a retry.
		test.setTimeout(60_000);
		await page.goto("/workflows");
		await page
			.getByPlaceholder("Search by name, description, or category...")
			.fill(workflowName);
		await page
			.getByRole("button", {
				name: `${workflowName} actions`,
				exact: true,
			})
			.click();
		await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
		const dialog = page.getByRole("dialog", {
			name: "Edit Workflow Settings",
		});
		await dialog.getByRole("tab", { name: "Access", exact: true }).click();
		await dialog
			.getByRole("link", { name: "Review conversion proposal" })
			.click();

		await expect(page).toHaveURL(/\/workflows\/convert\?workflows=/);
		await expect(
			page.getByRole("heading", { name: "1 workflow", exact: true }),
		).toBeVisible();
		const analyzeAction = page.getByRole("button", {
			name: "Analyze selected workflows",
		});
		await expect(analyzeAction).toBeVisible();
		const backBounds = await page
			.getByRole("link", { name: "Back to workflows" })
			.boundingBox();
		const analyzeBounds = await analyzeAction.boundingBox();
		expect(backBounds).not.toBeNull();
		expect(analyzeBounds).not.toBeNull();
		expect(Math.abs(backBounds!.y - analyzeBounds!.y)).toBeLessThanOrEqual(
			2,
		);
		expect(
			Math.abs(backBounds!.height - analyzeBounds!.height),
		).toBeLessThanOrEqual(2);
		expect(
			analyzeBounds!.x - (backBounds!.x + backBounds!.width),
		).toBeGreaterThanOrEqual(0);
		expect(
			analyzeBounds!.x - (backBounds!.x + backBounds!.width),
		).toBeLessThanOrEqual(12);
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-scope-desktop.png"),
			fullPage: true,
		});
		await page
			.getByRole("button", { name: "Analyze selected workflows" })
			.click();
		// Analysis is durable background work; wait for its user-visible result.
		await page.getByRole("heading", { name: "Review proposals" }).waitFor();
		const resultUrl = page.url();
		expect(resultUrl).toContain("job=");
		await page
			.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			})
			.click();
		await expect(
			page.getByText("organizations.read", { exact: true }).first(),
		).toBeVisible();
		await expect(page.getByRole("radiogroup")).toHaveCount(0);
		await expect(
			page.getByText(
				"Conversion preserves this identity, its roles, and its organization reach.",
			),
		).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-review-desktop.png"),
			fullPage: true,
		});
		const desktopViewport = page.viewportSize();
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= window.innerWidth,
			),
		).toBe(true);
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-review-mobile.png"),
			fullPage: true,
		});
		await page
			.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			})
			.click();
		const details = page.getByRole("dialog", {
			name: "Workflow details",
			exact: true,
		});
		await expect(details).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-details-mobile.png"),
			fullPage: true,
		});
		await details
			.getByRole("button", { name: "Close", exact: true })
			.click();
		await expect(
			page.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			}),
		).toBeFocused();
		if (desktopViewport) await page.setViewportSize(desktopViewport);

		await page
			.getByRole("checkbox", { name: `Select ${workflowName}` })
			.check();
		const reviewAction = page.getByRole("button", {
			name: /Review selected/,
		});
		const actionBounds = await reviewAction.boundingBox();
		expect(actionBounds).not.toBeNull();
		expect(actionBounds!.y + actionBounds!.height).toBeLessThanOrEqual(
			page.viewportSize()!.height,
		);
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-selected-desktop.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: /Review selected/ }).click();
		const confirmation = page.getByRole("alertdialog", {
			name: "Confirm reviewed changes",
		});
		await expect(confirmation).toBeVisible();
		await expect(
			confirmation.getByText(
				/Their current run identities and reach are preserved\./,
			),
		).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-confirm-desktop.png"),
			fullPage: true,
		});
		await confirmation
			.getByRole("button", {
				name: "Apply reviewed changes",
				exact: true,
			})
			.click();
		await page.getByText(/requested change completed/i).waitFor();
		await expect(
			page.getByRole("heading", { name: "Requested changes completed" }),
		).toBeVisible();
		await expect(
			page.getByText(/requested change completed/i),
		).toBeVisible();
		await page.screenshot({
			animations: "disabled",
			path: testInfo.outputPath("conversion-completed-desktop.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: "Return to review" }).click();
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();

		await page.goto(resultUrl);
		await expect(
			page.getByRole("heading", { name: "Review proposals" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", {
				name: `Review ${workflowName}`,
				exact: true,
			}),
		).toBeVisible();
	});

	test("WORKFLOW-CONVERSION-02 approves a pending Solution permission request", async ({
		page,
		api,
	}, testInfo) => {
		test.setTimeout(120_000);
		const requestSuffix = randomUUID().replaceAll("-", "").slice(0, 12);
		const slug = `e2e-permission-request-${requestSuffix}`;
		const requestWorkflowName = `solution_permission_request_${requestSuffix}`;
		const setTheme = async (theme: "light" | "dark") => {
			await page.evaluate(
				(value) => localStorage.setItem("theme", value),
				theme,
			);
			await page.reload();
			if (theme === "dark")
				await expect(page.locator("html")).toHaveClass(/dark/);
			else await expect(page.locator("html")).not.toHaveClass(/dark/);
		};
		const openAccess = async () => {
			await page.goto("/workflows");
			await page
				.getByPlaceholder("Search by name, description, or category...")
				.fill(requestWorkflowName);
			await page
				.getByRole("button", {
					name: `${requestWorkflowName} actions`,
					exact: true,
				})
				.click();
			await page
				.getByRole("menuitem", { name: "View Settings", exact: true })
				.click();
			const settings = page.getByRole("dialog", {
				name: "Edit Workflow Settings",
			});
			await settings
				.getByRole("tab", { name: "Access", exact: true })
				.click();
			return settings;
		};
		await page.goto("/workflows");
		await setTheme("light");
		const created = await api.post("/api/solutions", {
			data: {
				slug,
				name: slug.toUpperCase(),
				organization_id: null,
				global_repo_access: false,
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		const solutionId = (await created.json()).id as string;

		try {
			const deploy = await api.post(
				`/api/solutions/${solutionId}/deploy?force=true`,
				{
					multipart: {
						file: {
							name: "solution.zip",
							mimeType: "application/zip",
							buffer: solutionPermissionRequestZip(
								slug,
								requestWorkflowName,
							),
						},
					},
				},
			);
			expect(deploy.status(), await deploy.text()).toBe(202);
			await waitForDeploy(
				api,
				(await deploy.json()).deploy_job_id as string,
			);

			const dialog = await openAccess();
			await expect(
				dialog.getByRole("heading", {
					name: "Solution Permission Request",
				}),
			).toBeVisible();
			await expect(
				dialog.getByText("Pending", { exact: true }),
			).toBeVisible();
			await expect(
				dialog.getByText(
					"Declared permissions: Read Configuration, Read Integrations, Read Organizations, Read Tables",
				),
			).toBeVisible();
			await expect(
				dialog.getByRole("link", { name: "Review Permission Request" }),
			).toHaveAttribute(
				"href",
				expect.stringContaining("/workflows/convert?workflows="),
			);
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-access-desktop-light.png",
				),
				fullPage: true,
			});
			await page.setViewportSize({ width: 390, height: 844 });
			expect(
				await page.evaluate(
					() =>
						document.documentElement.scrollWidth <=
						window.innerWidth,
				),
			).toBe(true);
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-access-mobile-light.png",
				),
				fullPage: true,
			});
			await setTheme("dark");
			await openAccess();
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-access-mobile-dark.png",
				),
				fullPage: true,
			});
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-access-desktop-dark.png",
				),
				fullPage: true,
			});
			await setTheme("light");
			await openAccess();
			await dialog
				.getByRole("link", { name: "Review Permission Request" })
				.click();
			await page
				.getByRole("button", { name: "Analyze selected workflows" })
				.click();
			await page
				.getByRole("heading", { name: "Review proposals" })
				.waitFor();
			await page
				.getByRole("button", {
					name: `Review ${requestWorkflowName}`,
					exact: true,
				})
				.click();
			await expect(
				page.getByRole("heading", {
					name: "Solution Permission Request",
				}),
			).toBeVisible();
			await expect(
				page.getByText("Pending", { exact: true }),
			).toBeVisible();
			await expect(
				page.getByText(
					"Declared permissions: Read Configuration, Read Integrations, Read Organizations, Read Tables",
				),
			).toBeVisible();
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-review-desktop-light.png",
				),
				fullPage: true,
			});
			await page.setViewportSize({ width: 390, height: 844 });
			await page
				.getByRole("button", {
					name: `Review ${requestWorkflowName}`,
					exact: true,
				})
				.click();
			const details = page.getByRole("dialog", {
				name: "Workflow details",
			});
			await expect(details).toBeVisible();
			expect(
				await page.evaluate(
					() =>
						document.documentElement.scrollWidth <=
						window.innerWidth,
				),
			).toBe(true);
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-review-mobile-light.png",
				),
				fullPage: true,
			});
			await setTheme("dark");
			await page
				.getByRole("button", {
					name: `Review ${requestWorkflowName}`,
					exact: true,
				})
				.click();
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-review-mobile-dark.png",
				),
				fullPage: true,
			});
			await details
				.getByRole("button", { name: "Close", exact: true })
				.click();
			await setTheme("light");
			await page.setViewportSize({ width: 1440, height: 900 });
			await page
				.getByRole("checkbox", {
					name: `Select ${requestWorkflowName}`,
				})
				.check();
			await page.getByRole("button", { name: /Review selected/ }).click();
			const confirmation = page.getByRole("alertdialog", {
				name: "Confirm reviewed changes",
			});
			await expect(confirmation).toContainText(
				"Apply approves the exact requested mode and permissions shown in review. It does not approve analyzer suggestions.",
			);
			await confirmation
				.getByRole("checkbox", {
					name: "I approve the exact requested Solution permissions.",
				})
				.check();
			await confirmation
				.getByRole("button", { name: "Apply reviewed changes" })
				.click();
			await page.getByText(/requested change completed/i).waitFor();
			await page
				.getByRole("button", { name: "Return to review" })
				.click();
			await page
				.getByRole("heading", { name: "Review proposals" })
				.waitFor();
			await page
				.getByRole("button", {
					name: `Review ${requestWorkflowName}`,
					exact: true,
				})
				.click();
			await expect(
				page.getByText("Approved", { exact: true }),
			).toBeVisible();
			await page.screenshot({
				animations: "disabled",
				path: testInfo.outputPath(
					"solution-request-review-approved-desktop-light.png",
				),
				fullPage: true,
			});
			await page.goto("/workflows");
			await page
				.getByPlaceholder("Search by name, description, or category...")
				.fill(requestWorkflowName);
			await page
				.getByRole("button", {
					name: `${requestWorkflowName} actions`,
					exact: true,
				})
				.click();
			await page
				.getByRole("menuitem", { name: "View Settings", exact: true })
				.click();
			const approvedDialog = page.getByRole("dialog", {
				name: "Edit Workflow Settings",
			});
			await approvedDialog
				.getByRole("tab", { name: "Access", exact: true })
				.click();
			await expect(
				approvedDialog.getByText("Approved", { exact: true }),
			).toBeVisible();
			await expect(
				approvedDialog.getByRole("link", {
					name: "Review conversion proposal",
				}),
			).toBeVisible();
		} finally {
			const removed = await api.delete(`/api/solutions/${solutionId}`, {
				params: { confirm: slug },
			});
			expect([200, 204, 404], await removed.text()).toContain(
				removed.status(),
			);
		}
	});
});
