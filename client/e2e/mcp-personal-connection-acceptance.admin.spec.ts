/**
 * MCP Personal Connection Acceptance (Admin)
 *
 * Covers the real user-facing delegated MCP OAuth journey against the local
 * scheduler fixture provider: create server/connection metadata through the
 * API, initiate Connect from the browser UI, complete Bifrost's real MCP OAuth
 * callback, verify the persisted user credential, then disconnect through the
 * UI and prove the credential is removed.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import type { Page } from "./fixtures/api-fixture";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

type AllCredentials = {
	org1: { id: string; name: string };
};

type MCPServer = { id: string; name: string };
type MCPConnection = { id: string; server_id: string; organization_id: string };
type UserMCPCredential = {
	connection_id: string;
	consent_granted_at: string | null;
	granted_scopes?: string[] | null;
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const SERVER_NAME = `E2E MCP Personal ${UNIQUE}`;
const FIXTURE_ORIGIN = "http://scheduler-fixtures:8080";
const FIXTURE_SCOPE = "fixture.read";

async function expectOk(
	response: { ok(): boolean; status(): number },
	method: string,
	path: string,
) {
	expect(
		response.ok(),
		`${method} ${path} returned ${response.status()}`,
	).toBe(true);
}

function readCredentials(): AllCredentials {
	return JSON.parse(
		readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
	) as AllCredentials;
}

async function createMcpServer(api: AuthedApi): Promise<MCPServer> {
	const path = "/api/mcp-servers";
	const response = await api.post(path, {
		data: {
			name: SERVER_NAME,
			server_url: `${FIXTURE_ORIGIN}/mcp`,
			oauth_provider: {
				oauth_flow_type: "authorization_code",
				authorization_url: `${FIXTURE_ORIGIN}/oauth/authorize`,
				token_url: `${FIXTURE_ORIGIN}/oauth/token`,
				scopes: [FIXTURE_SCOPE],
				audience: null,
			},
			discovery_metadata: {
				authorization_endpoint: `${FIXTURE_ORIGIN}/oauth/authorize`,
				token_endpoint: `${FIXTURE_ORIGIN}/oauth/token`,
				scopes_supported: [FIXTURE_SCOPE],
				grant_types_supported: ["authorization_code"],
				_source: "scheduler-fixture",
			},
			organization_id: null,
			is_active: true,
		},
	});
	await expectOk(response, "POST", path);
	return (await response.json()) as MCPServer;
}

async function createMcpConnection(
	api: AuthedApi,
	serverId: string,
	organizationId: string,
): Promise<MCPConnection> {
	const path = "/api/mcp-connections";
	const response = await api.post(path, {
		data: {
			server_id: serverId,
			organization_id: organizationId,
			client_id: "scheduler-fixture-client",
			client_secret: "scheduler-fixture-secret",
			available_in_chat: true,
			available_to_autonomous: false,
		},
	});
	await expectOk(response, "POST", path);
	return (await response.json()) as MCPConnection;
}

async function listUserCredentials(
	api: AuthedApi,
): Promise<UserMCPCredential[]> {
	const path = "/api/me/mcp-connections";
	const response = await api.get(path);
	await expectOk(response, "GET", path);
	return (await response.json()) as UserMCPCredential[];
}

async function expectCredential(
	api: AuthedApi,
	connectionId: string,
	present: boolean,
) {
	await expect
		.poll(
			async () => {
				const credentials = await listUserCredentials(api);
				return credentials.some(
					(credential) => credential.connection_id === connectionId,
				);
			},
			{ message: "personal MCP credential persistence" },
		)
		.toBe(present);
}

async function openConnections(page: Page) {
	await page.goto("/user-settings/connections");
	await expect(
		page.getByRole("heading", { name: "User Settings" }),
	).toBeVisible({ timeout: 10_000 });
	await expect(
		page.getByRole("heading", { name: "My Connections" }),
	).toBeVisible({
		timeout: 10_000,
	});
}

function serviceRow(page: Page) {
	return page.getByRole("row", { name: new RegExp(SERVER_NAME) });
}

test.describe("MCP personal connection acceptance", () => {
	let server: MCPServer | undefined;
	let connection: MCPConnection | undefined;

	test.beforeAll(async ({ api }) => {
		const credentials = readCredentials();
		server = await createMcpServer(api);
		connection = await createMcpConnection(
			api,
			server.id,
			credentials.org1.id,
		);
	});

	test.afterAll(async ({ api }) => {
		if (connection?.id) {
			const response = await api.delete(
				`/api/mcp-connections/${connection.id}`,
			);
			expect(
				[204, 404],
				`DELETE /api/mcp-connections/{connection_id} returned ${response.status()}`,
			).toContain(response.status());
		}
		if (server?.id) {
			const response = await api.delete(`/api/mcp-servers/${server.id}`, {
				params: { hard: true },
			});
			expect(
				[204, 404],
				`DELETE /api/mcp-servers/{server_id} returned ${response.status()}`,
			).toContain(response.status());
		}
	});

	test("connects through MCP OAuth callback and disconnects persisted personal credentials", async ({
		page,
		api,
	}) => {
		expect(connection).toBeDefined();
		await expectCredential(api, connection!.id, false);

		await openConnections(page);
		const row = serviceRow(page);
		await expect(row).toBeVisible({ timeout: 10_000 });
		await expect(row).toContainText("Not connected");

		const popupPromise = page.waitForEvent("popup");
		await row.getByRole("button", { name: "Connect", exact: true }).click();
		const popup = await popupPromise;

		await expectCredential(api, connection!.id, true);
		if (!popup.isClosed()) await popup.close();
		await expect(row).toContainText("Connected", { timeout: 10_000 });

		await page.reload();
		await expect(serviceRow(page)).toContainText("Connected", {
			timeout: 10_000,
		});

		await serviceRow(page)
			.getByRole("button", { name: "Disconnect" })
			.click();
		await expectCredential(api, connection!.id, false);
		await expect(serviceRow(page)).toContainText("Not connected", {
			timeout: 10_000,
		});
	});
});
