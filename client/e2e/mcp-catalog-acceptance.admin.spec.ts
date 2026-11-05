/**
 * MCP Catalog Acceptance (Admin)
 *
 * Covers the real admin UI happy path for an OAuth-backed service MCP
 * connection against the local scheduler fixture: seed owned server and
 * connection metadata through the API, activate the service connection through
 * the browser, refresh the tool catalog through the browser, verify API and
 * reload persistence, disconnect the service, then delete owned records.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import { expect, test, type AuthedApi } from "./fixtures/api-fixture";

type Credentials = {
	org1: { id: string; name: string };
};

type MCPServer = {
	id: string;
	name: string;
	server_url: string;
	oauth_provider_id: string | null;
	oauth_flow_type?: string | null;
};

type MCPConnectionTool = {
	id: string;
	tool_name: string;
	tool_schema: Record<string, unknown> | null;
	enabled: boolean;
	disabled_reason: string | null;
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
	tools?: MCPConnectionTool[] | null;
};

type RefreshToolsResponse = {
	total: number;
	enabled: number;
	disabled: number;
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const SERVER_NAME = `E2E MCP Catalog ${UNIQUE}`;
const FIXTURE_ORIGIN = "http://scheduler-fixtures:8080";
const FIXTURE_SCOPE = "fixture.read";
const FIXTURE_TOOL_NAME = "scheduler_fixture_echo";
// The catalog stores the vendor's tool descriptor, not the bare input schema.
const FIXTURE_TOOL_SCHEMA = {
	description: "Echoes deterministic scheduler fixture input.",
	inputSchema: {
		type: "object",
		properties: { message: { type: "string" } },
		required: ["message"],
	},
};

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

async function createMcpServer(api: AuthedApi): Promise<MCPServer> {
	const response = await api.post("/api/mcp-servers", {
		data: {
			name: SERVER_NAME,
			server_url: `${FIXTURE_ORIGIN}/mcp`,
			oauth_provider: {
				oauth_flow_type: "client_credentials",
				token_url: `${FIXTURE_ORIGIN}/oauth/token`,
				scopes: [FIXTURE_SCOPE],
				audience: null,
			},
			discovery_metadata: {
				token_endpoint: `${FIXTURE_ORIGIN}/oauth/token`,
				scopes_supported: [FIXTURE_SCOPE],
				grant_types_supported: ["client_credentials"],
				_source: "scheduler-fixture",
			},
			organization_id: null,
			is_active: true,
		},
	});
	await expectStatus(response, [201], "create MCP catalog server");
	return (await response.json()) as MCPServer;
}

async function createMcpConnection(
	api: AuthedApi,
	serverId: string,
	organizationId: string,
): Promise<MCPConnection> {
	const response = await api.post("/api/mcp-connections", {
		data: {
			server_id: serverId,
			organization_id: organizationId,
			client_id: "scheduler-fixture-client",
			client_secret: "scheduler-fixture-secret",
			available_in_chat: true,
			available_to_autonomous: true,
		},
	});
	await expectStatus(response, [201], "create MCP catalog connection");
	return (await response.json()) as MCPConnection;
}

async function readConnection(
	api: AuthedApi,
	connectionId: string,
): Promise<MCPConnection> {
	const response = await api.get(`/api/mcp-connections/${connectionId}`);
	await expectStatus(response, [200], "read MCP catalog connection");
	return (await response.json()) as MCPConnection;
}

async function deleteConnection(api: AuthedApi, connectionId: string) {
	const response = await api.delete(`/api/mcp-connections/${connectionId}`);
	await expectStatus(
		response,
		[204, 404],
		"delete MCP catalog connection cleanup",
	);
}

async function deleteServer(api: AuthedApi, serverId: string) {
	const response = await api.delete(`/api/mcp-servers/${serverId}`, {
		params: { hard: true },
	});
	await expectStatus(
		response,
		[204, 404],
		"delete MCP catalog server cleanup",
	);
}

test.describe("MCP service catalog acceptance", () => {
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

	test("activates, refreshes, reloads, disconnects, and deletes a service MCP catalog", async ({
		page,
		api,
	}) => {
		const credentials = readCredentials();
		const server = await createMcpServer(api);
		serverId = server.id;
		expect(server.oauth_provider_id).toBeTruthy();
		expect(server.oauth_flow_type).toBe("client_credentials");

		const connection = await createMcpConnection(
			api,
			server.id,
			credentials.org1.id,
		);
		connectionId = connection.id;
		expect(connection.service_oauth_token_id).toBeNull();
		expect(connection.tools ?? []).toHaveLength(0);

		await page.goto(
			`/mcp-servers/${server.id}/connections/${connection.id}/edit`,
		);
		await expect(
			page.getByRole("heading", {
				name: `${credentials.org1.name} connection`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.getByText("Not connected")).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Refresh catalog" }),
		).toBeDisabled();

		await page.getByRole("button", { name: "Activate connection" }).click();
		await expect(page.getByText("Connected")).toBeVisible({
			timeout: 15_000,
		});
		await expect(
			page.getByRole("button", { name: "Refresh catalog" }),
		).toBeEnabled();

		const activatedConnection = await readConnection(api, connection.id);
		expect(activatedConnection.service_oauth_token_id).toBeTruthy();
		expect(activatedConnection.tools ?? []).toHaveLength(0);

		const refreshResponsePromise = page.waitForResponse((response) => {
			return (
				response
					.url()
					.includes(
						`/api/mcp-connections/${connection.id}/refresh-tools`,
					) && response.request().method() === "POST"
			);
		});
		await page.getByRole("button", { name: "Refresh catalog" }).click();
		const refreshResponse = await refreshResponsePromise;
		await expectStatus(refreshResponse, [200], "refresh MCP catalog");
		const refreshResult =
			(await refreshResponse.json()) as RefreshToolsResponse;
		expect(refreshResult).toMatchObject({
			total: 1,
			enabled: 1,
			disabled: 0,
		});

		await expect(
			page.getByRole("list", { name: "Connection tools" }),
		).toBeVisible({
			timeout: 15_000,
		});
		await expect(page.getByLabel(FIXTURE_TOOL_NAME)).toBeChecked();
		await expect(page.getByText("1 tools · 1 enabled")).toBeVisible();

		const refreshedConnection = await readConnection(api, connection.id);
		expect(refreshedConnection.service_oauth_token_id).toBe(
			activatedConnection.service_oauth_token_id,
		);
		expect(refreshedConnection.tools ?? []).toHaveLength(1);
		expect(refreshedConnection.tools?.[0]).toMatchObject({
			tool_name: FIXTURE_TOOL_NAME,
			tool_schema: FIXTURE_TOOL_SCHEMA,
			enabled: true,
			disabled_reason: null,
		});

		await page.reload();
		await expect(
			page.getByRole("heading", {
				name: `${credentials.org1.name} connection`,
			}),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.getByText("Connected")).toBeVisible();
		await expect(page.getByLabel(FIXTURE_TOOL_NAME)).toBeChecked();
		await expect(page.getByText("1 tools · 1 enabled")).toBeVisible();

		await page.getByRole("button", { name: "Disconnect" }).click();
		await expect(page.getByText("Not connected")).toBeVisible({
			timeout: 15_000,
		});
		await expect(
			page.getByRole("button", { name: "Refresh catalog" }),
		).toBeDisabled();
		const disconnectedConnection = await readConnection(api, connection.id);
		expect(disconnectedConnection.service_oauth_token_id).toBeNull();
		expect(disconnectedConnection.tools ?? []).toHaveLength(1);
		expect(disconnectedConnection.tools?.[0].tool_name).toBe(
			FIXTURE_TOOL_NAME,
		);

		await page.getByRole("button", { name: "Delete connection" }).click();
		const deleteDialog = page.getByRole("alertdialog", {
			name: "Delete this connection?",
		});
		await expect(deleteDialog).toBeVisible();
		await deleteDialog
			.getByRole("button", { name: "Delete connection" })
			.click();
		await expect(page).toHaveURL(new RegExp(`/mcp-servers/${server.id}$`));
		connectionId = undefined;

		const missingConnection = await api.get(
			`/api/mcp-connections/${connection.id}`,
		);
		await expectStatus(
			missingConnection,
			[404],
			"read deleted MCP catalog connection",
		);
	});
});
