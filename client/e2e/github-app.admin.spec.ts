import { createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import type { BrowserContext } from "@playwright/test";
import { test, expect, type AuthedApi, type APIResponse } from "./fixtures/api-fixture";
import { createFreshApiContext, createFreshBrowserContext } from "./fixtures/human-auth";
import { registerAndSetupMFA } from "./setup/auth-helpers";
import type { UserCredentials } from "./fixtures/users";
import type { components } from "../src/lib/v1";

const APP_ID = 8000123;
const INSTALLATION_ID = 70001;
const SECRET = "synthetic-browser-webhook-secret-32-characters";
const namespace = randomUUID().slice(0, 8);
let admin: AuthedApi;
let adminContext: BrowserContext;
let reader: UserCredentials;
let readerId: string;
let organizationId: string;
let workflowId: string;
const sourcePath = `github_app_example_${namespace}.py`;

async function expectOk(response: APIResponse) {
    expect(response.ok(), `${response.status()}: ${await response.text()}`).toBeTruthy();
}

test.beforeAll(async ({ browser }) => {
    ({ api: admin, context: adminContext } = await createFreshApiContext(browser, "platform_admin"));
    const organization = await admin.post("/api/organizations", { data: { name: `GitHub App ${namespace}` } });
    await expectOk(organization);
    organizationId = (await organization.json()).id;
    const user = { key: `github_app_${namespace}`, email: `github-app-${namespace}@example.com`,
        name: "GitHub App Test Operator", password: `GitHubApp-${namespace}-Password!`, isSuperuser: true };
    const created = await admin.post("/api/users", { data: { email: user.email, name: user.name, is_superuser: true } });
    await expectOk(created);
    readerId = (await created.json()).id;
    reader = await registerAndSetupMFA(user);
    const assignments = await admin.get(`/api/users/${readerId}/role-assignments`);
    await expectOk(assignments);
    const current = await assignments.json() as components["schemas"]["UserRoleAssignmentsResponse"];
    const granted = await admin.put(`/api/users/${readerId}/role-assignments`, { data: {
        base_role_id: current.base_role.id,
        additional: [...current.additional.map((item) => ({ role_id: item.role_id, boundaries: item.boundaries.map((boundary) => ({ kind: boundary.kind, organization_id: boundary.organization_id })) })),
                     { role_id: "00000000-0000-0000-0000-000000000008" }],
    } });
    await expectOk(granted);
    // Existing announcements have their own journey. Start this feature's
    // operator with receipts so a delayed onboarding dialog cannot cover it.
    const operator = await createFreshApiContext(browser, reader);
    try {
        const response = await operator.api.get("/api/product-updates");
        await expectOk(response);
        const feed = await response.json() as components["schemas"]["ProductUpdatesFeedResponse"];
        const entryIds = feed.bundle.entries.filter((entry) => entry.in_app !== false).map((entry) => entry.id);
        if (entryIds.length) await expectOk(await operator.api.post("/api/product-updates/receipts", { data: { entry_ids: entryIds } }));
    } finally {
        await operator.context.close();
    }
    const content = readFileSync("/app/examples/github-app/workflows/github_events.py", "utf8");
    await expectOk(await admin.put("/api/files/editor/content", { data: { path: sourcePath, content, encoding: "utf-8" } }));
    const registered = await admin.post("/api/workflows/register", { data: { path: sourcePath, function_name: "github_pr_triage", organization_id: organizationId } });
    await expectOk(registered);
    workflowId = (await registered.json()).id;
});

test.afterAll(async ({ browser }) => {
    if (reader) {
        const fresh = await createFreshApiContext(browser, reader);
        try { await expectOk(await fresh.api.delete("/api/github-app/config")); }
        finally { await fresh.context.close(); }
    }
    if (organizationId) await admin.delete(`/api/organizations/${organizationId}`);
    if (readerId) await admin.delete(`/api/users/${readerId}`);
    if (workflowId) await admin.delete("/api/files/editor", { params: { path: sourcePath } });
    await adminContext?.close();
});

test("manually connects an App, verifies its inventory, and runs the read-only PR example", async ({ browser }) => {
    const context = await createFreshBrowserContext(browser, reader);
    const operator = await createFreshApiContext(browser, reader);
    let subscriptionId: string | undefined;
    try {
        const page = await context.newPage();
        await page.goto("/settings/github-app");
        await page.getByLabel("App ID", { exact: true }).fill(String(APP_ID));
        await page.getByLabel("Private Key", { exact: true }).fill(readFileSync("/fixture-tls/github-app-key.pem", "utf8"));
        await page.getByLabel("Webhook Secret", { exact: true }).fill(SECRET);
        await page.getByRole("button", { name: "Verify And Save App", exact: true }).click();
        await expect(page.getByText(/Configured: bifrost-fixture-app/)).toBeVisible();
        await expect(page.getByLabel("Private Key", { exact: true })).toHaveValue("");
        await page.getByLabel("Bifrost Organization", { exact: true }).selectOption(organizationId);
        await page.getByLabel("Installation ID", { exact: true }).fill(String(INSTALLATION_ID));
        await page.getByRole("button", { name: "Verify And Bind Installation", exact: true }).click();
        await expect(page.getByRole("heading", { name: "fixture-70001 — stale" })).toBeVisible();
        // Follow the completion notification's actual query refresh. The
        // assertions then check the resulting view, not a job-duration SLO.
        const refreshedInventory = page.waitForResponse(async (response) => {
            const url = new URL(response.url());
            if (url.pathname !== "/api/github-app/installations" || url.searchParams.get("organization_id") !== organizationId || !response.ok()) return false;
            const inventory = await response.json() as components["schemas"]["GitHubAppInstallations"];
            return inventory.installations.some((installation) => installation.installation_id === INSTALLATION_ID && installation.status === "active");
        });
        await page.getByRole("button", { name: "Refresh And Recover", exact: true }).click();
        await refreshedInventory;
        await expect(page.getByRole("heading", { name: "fixture-70001 — active" })).toBeVisible();
        await expect(page.getByText("fixture/repository-70001", { exact: true })).toBeVisible();
        const listed = await operator.api.get("/api/github-app/installations", { params: { organization_id: organizationId } });
        await expectOk(listed);
        const inventory = await listed.json() as components["schemas"]["GitHubAppInstallations"];
        const sourceId = inventory.installations[0].event_source_id;
        const subscription = await operator.api.post(`/api/events/sources/${sourceId}/subscriptions`, { data: {
            workflow_id: workflowId, event_type: "github.pull_request.opened", input_mapping: { event: "{{ payload }}" },
        } });
        await expectOk(subscription);
        subscriptionId = (await subscription.json()).id;
        const deliveryId = randomUUID();
        const body = JSON.stringify({ action: "opened", installation: { id: INSTALLATION_ID }, repository: { id: 80001 },
            pull_request: { number: 42, title: "Read-only fixture example", html_url: "https://github.com/fixture/repository-70001/pull/42" } });
        const response = await operator.api.post(`/api/hooks/github-app/${APP_ID}`, { data: body, headers: {
            "Content-Type": "application/json", "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": deliveryId,
            "X-Hub-Signature-256": `sha256=${createHmac("sha256", SECRET).update(body).digest("hex")}`,
        } });
        expect(response.status()).toBe(202);
        await expect.poll(async () => {
            const events = await operator.api.get(`/api/events/sources/${sourceId}/events`);
            const result = await events.json() as components["schemas"]["EventListResponse"];
            return result.items[0]?.success_count;
        }).toBe(1);
        await page.screenshot({ path: "playwright-results/github-app-setup-health.png", fullPage: true });
    } finally {
        if (subscriptionId) await operator.api.delete(`/api/events/subscriptions/${subscriptionId}`);
        await operator.context.close();
        await context.close();
    }
});
