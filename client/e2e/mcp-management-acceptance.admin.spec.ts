/**
 * MCP Management Acceptance (Admin)
 *
 * Covers the real admin UI happy path for auth-free external MCP management:
 * create a server template, create an org connection, edit connection metadata
 * and availability, verify persistence after reload, then delete the owned
 * connection and server. No third-party network, OAuth callback, or credential
 * response body is logged.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import type { Page } from "./fixtures/api-fixture";
import { expect, test, type AuthedApi } from "./fixtures/api-fixture";

type Credentials = {
	org1: { id: string; name: string };
};

type MCPServer = {
	id: string;
	name: string;
	server_url: string;
	oauth_provider_id: string | null;
	discovery_metadata: Record<string, unknown> | null;
	is_active: boolean;
};

type MCPConnection = {
	id: string;
	server_id: string;
	organization_id: string;
	client_id: string;
	server_url_override: string | null;
	available_in_chat: boolean;
	available_to_autonomous: boolean;
	service_oauth_token_id: string | null;
	tools?: unknown[] | null;
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const SERVER_NAME = `E2E MCP Management ${UNIQUE}`;
const SERVER_URL = `https://mcp-management-${UNIQUE}.example.test/mcp`;
const UPDATED_CLIENT_ID = `e2e-management-client-updated-${UNIQUE}`;
const UPDATED_URL_OVERRIDE = `https://mcp-management-${UNIQUE}.example.test/org-one/mcp`;

function readCredentials(): Credentials {
	return JSON.parse(
		readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
	) as Credentials;
}

async function expectStatus(
	response: { status(): number },
	allowed: number[],
	label: string,
) {
	expect(allowed, `${label} returned ${response.status()}`).toContain(
		response.status(),
	);
}

async function readServer(
	api: AuthedApi,
	serverId: string,
): Promise<MCPServer> {
	const response = await api.get(`/api/mcp-servers/${serverId}`);
	await expectStatus(response, [200], "read MCP server");
	return (await response.json()) as MCPServer;
}

async function readConnection(
	api: AuthedApi,
	connectionId: string,
): Promise<MCPConnection> {
	const response = await api.get(`/api/mcp-connections/${connectionId}`);
	await expectStatus(response, [200], "read MCP connection");
	return (await response.json()) as MCPConnection;
}

async function deleteConnection(api: AuthedApi, connectionId: string) {
	const response = await api.delete(`/api/mcp-connections/${connectionId}`);
	await expectStatus(response, [204, 404], "delete MCP connection cleanup");
}

async function deleteServer(api: AuthedApi, serverId: string) {
	const response = await api.delete(`/api/mcp-servers/${serverId}`, {
		params: { hard: true },
	});
	await expectStatus(response, [204, 404], "delete MCP server cleanup");
}

async function openMcpServers(page: Page) {
	await page.goto("/mcp-servers");
	await expect(
		page.getByRole("heading", { name: "MCP Servers", exact: true }),
	).toBeVisible({ timeout: 15_000 });
}

function serverRow(page: Page) {
	return page.getByRole("row", { name: new RegExp(SERVER_NAME) });
}

test.describe("MCP management acceptance", () => {
	let serverId: string | undefined;
	let connectionId: string | undefined;

	test.afterEach(async ({ api }) => {
		if (connectionId) {
			await deleteConnection(api, connectionId);
			connectionId = undefined;
		}
		if (serverId) {
			await deleteServer(api, serverId);
			serverId = undefined;
		}
	});

	test("creates, edits, reloads, and deletes an auth-free MCP server connection", async ({
		page,
		api,
	}) => {
		const credentials = readCredentials();

		await openMcpServers(page);
		await page
			.getByRole("button", { name: "New Server", exact: true })
			.first()
			.click();

		const createServerDialog = page.getByRole("dialog", {
			name: "New MCP Server",
		});
		await expect(createServerDialog).toBeVisible();
		await createServerDialog.getByLabel("Display name").fill(SERVER_NAME);
		await createServerDialog.getByLabel("Server URL").fill(SERVER_URL);
		await createServerDialog
			.getByRole("button", { name: "Create Server" })
			.click();

		await expect(page).toHaveURL(/\/mcp-servers\/[^/]+$/);
		serverId = new URL(page.url()).pathname.split("/").pop();
		expect(serverId, "created MCP server id from detail URL").toBeTruthy();

		await expect(
			page.getByRole("heading", { name: SERVER_NAME }),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.getByText(SERVER_URL)).toBeVisible();
		await expect(page.getByText("No connections yet")).toBeVisible();

		const persistedServer = await readServer(api, serverId!);
		expect(persistedServer).toMatchObject({
			name: SERVER_NAME,
			server_url: SERVER_URL,
			oauth_provider_id: null,
			discovery_metadata: null,
			is_active: true,
		});

		await page.getByRole("button", { name: "New Connection" }).click();
		const createConnectionDialog = page.getByRole("dialog", {
			name: "New MCP Connection",
		});
		await expect(createConnectionDialog).toBeVisible();
		await createConnectionDialog
			.getByRole("combobox", { name: "Organization" })
			.click();
		await page.getByRole("option", { name: credentials.org1.name }).click();
		await createConnectionDialog
			.getByLabel("Client ID")
			.fill(`e2e-management-client-${UNIQUE}`);
		await createConnectionDialog
			.getByLabel("Client Secret")
			.fill(`e2e-management-secret-${UNIQUE}`);
		await createConnectionDialog
			.getByRole("button", { name: "Create" })
			.click();

		await expect(page).toHaveURL(
			new RegExp(`/mcp-servers/${serverId}/connections/[^/]+/edit$`),
		);
		const parts = new URL(page.url()).pathname.split("/");
		connectionId = parts[parts.indexOf("connections") + 1];
		expect(
			connectionId,
			"created MCP connection id from edit URL",
		).toBeTruthy();

		await expect(
			page.getByRole("heading", {
				name: `${credentials.org1.name} connection`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.getByText("Not connected")).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Refresh catalog" }),
		).toBeDisabled();

		await page.getByLabel("Client ID").fill(UPDATED_CLIENT_ID);
		await page.getByLabel("Server URL override").fill(UPDATED_URL_OVERRIDE);

		const chatSwitch = page.getByRole("switch", {
			name: "Available in user chat",
		});
		const autonomousSwitch = page.getByRole("switch", {
			name: "Available to autonomous agents",
		});
		if (!(await chatSwitch.isChecked())) await chatSwitch.click();
		if (!(await autonomousSwitch.isChecked()))
			await autonomousSwitch.click();

		await page.getByRole("button", { name: "Save" }).click();
		await expect(page.getByRole("button", { name: "Save" })).toBeEnabled({
			timeout: 15_000,
		});

		const persistedConnection = await readConnection(api, connectionId!);
		expect(persistedConnection).toMatchObject({
			id: connectionId,
			server_id: serverId,
			organization_id: credentials.org1.id,
			client_id: UPDATED_CLIENT_ID,
			server_url_override: UPDATED_URL_OVERRIDE,
			available_in_chat: true,
			available_to_autonomous: true,
			service_oauth_token_id: null,
		});
		expect(persistedConnection.tools ?? []).toHaveLength(0);

		await page.reload();
		await expect(
			page.getByRole("heading", {
				name: `${credentials.org1.name} connection`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.getByLabel("Client ID")).toHaveValue(
			UPDATED_CLIENT_ID,
		);
		await expect(page.getByLabel("Server URL override")).toHaveValue(
			UPDATED_URL_OVERRIDE,
		);
		await expect(chatSwitch).toBeChecked();
		await expect(autonomousSwitch).toBeChecked();
		await expect(page.getByText("No tools cached")).toBeVisible();

		await page
			.getByRole("link", { name: SERVER_NAME, exact: true })
			.click();
		await expect(page).toHaveURL(new RegExp(`/mcp-servers/${serverId}$`));
		await expect(
			page.getByRole("link", {
				name: `Manage ${credentials.org1.name} connection`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await expect(
			page.getByRole("heading", {
				name: credentials.org1.name,
				exact: true,
			}),
		).toBeVisible();
		await expect(page.getByText("User chat")).toBeVisible();
		await expect(page.getByText("Autonomous agents")).toBeVisible();
		await expect(page.getByText("Available", { exact: true })).toHaveCount(
			2,
		);

		await page
			.getByRole("link", {
				name: `Manage ${credentials.org1.name} connection`,
			})
			.click();
		await page.getByRole("button", { name: "Delete connection" }).click();
		const deleteConnectionDialog = page.getByRole("alertdialog", {
			name: "Delete this connection?",
		});
		await expect(deleteConnectionDialog).toBeVisible();
		await deleteConnectionDialog
			.getByRole("button", { name: "Delete connection" })
			.click();
		await expect(page).toHaveURL(new RegExp(`/mcp-servers/${serverId}$`));
		connectionId = undefined;
		await expect(page.getByText("No connections yet")).toBeVisible({
			timeout: 15_000,
		});

		const missingConnection = await api.get(
			`/api/mcp-connections/${persistedConnection.id}`,
		);
		await expectStatus(
			missingConnection,
			[404],
			"read deleted MCP connection",
		);

		await page.getByRole("button", { name: "Delete server" }).click();
		const deleteServerDialog = page.getByRole("alertdialog", {
			name: "Delete this MCP server?",
		});
		await expect(deleteServerDialog).toBeVisible();
		await deleteServerDialog
			.getByRole("button", { name: "Delete server" })
			.click();
		await expect(page).toHaveURL(/\/mcp-servers$/);
		serverId = undefined;

		await expect(serverRow(page)).toHaveCount(0);
		const missingServer = await api.get(
			`/api/mcp-servers/${persistedServer.id}`,
		);
		await expectStatus(missingServer, [404], "read deleted MCP server");
	});
});
