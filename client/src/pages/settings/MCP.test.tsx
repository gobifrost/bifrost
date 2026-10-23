import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test-utils";
import type { components } from "@/lib/v1";
import { MCP } from "./MCP";

type MCPConfigResponse = components["schemas"]["MCPConfigResponse"];

const api = vi.hoisted(() => ({
	useQuery: vi.fn(),
	useMutation: vi.fn(),
}));

const toast = vi.hoisted(() => ({
	success: vi.fn(),
	error: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: api,
}));

vi.mock("sonner", () => ({ toast }));

const config: MCPConfigResponse = {
	enabled: true,
	allowed_tool_ids: null,
	blocked_tool_ids: [],
	is_configured: true,
	configured_at: "2026-09-10T12:00:00.000Z",
	configured_by: "admin@example.com",
};

const tools = {
	tools: [
		{
			id: "workflow.run",
			name: "Run workflow",
			description: "Run a workflow",
			is_system: true,
		},
	],
};

const refetchConfig = vi.fn();
const refetchTools = vi.fn();
const saveMutation = { mutateAsync: vi.fn(), isError: false };
const deleteMutation = { mutateAsync: vi.fn(), isError: false, reset: vi.fn() };

function mockQueries({
	configError = false,
	toolsError = false,
	configData = config,
	toolsData = tools,
}: {
	configError?: boolean;
	toolsError?: boolean;
	configData?: MCPConfigResponse | null;
	toolsData?: typeof tools | null;
} = {}) {
	api.useQuery.mockImplementation((_method, path) => {
		if (path === "/api/mcp/config") {
			return {
				data: configData,
				isLoading: false,
				isError: configError,
				isFetching: false,
				refetch: refetchConfig,
			};
		}
		if (path === "/api/mcp/tools") {
			return {
				data: toolsData,
				isLoading: false,
				isError: toolsError,
				isFetching: false,
				refetch: refetchTools,
			};
		}
		throw new Error(`Unexpected query path ${path}`);
	});
}

beforeEach(() => {
	vi.clearAllMocks();
	mockQueries();
	saveMutation.mutateAsync.mockResolvedValue(config);
	saveMutation.isError = false;
	deleteMutation.mutateAsync.mockResolvedValue({
		enabled: true,
		allowed_tool_ids: null,
		blocked_tool_ids: [],
		is_configured: false,
	});
	deleteMutation.isError = false;
	api.useMutation.mockImplementation((_method, path) => {
		if (path === "/api/mcp/config") {
			return _method === "delete" ? deleteMutation : saveMutation;
		}
		throw new Error(`Unexpected mutation path ${path}`);
	});
});

describe("MCP settings", () => {
	it("shows the initial MCP configuration read error instead of controls", () => {
		mockQueries({ configData: null, configError: true });

		renderWithProviders(<MCP />);

		expect(screen.getByRole("alert")).toHaveTextContent(
			/Could not load MCP configuration/i,
		);
		expect(
			screen.getByRole("button", { name: /retry MCP configuration/i }),
		).toBeEnabled();
		expect(
			screen.queryByRole("switch", { name: "Enable MCP Access" }),
		).not.toBeInTheDocument();
	});

	it("keeps cached config visible and disables tool selection when tools fail", () => {
		mockQueries({ toolsError: true, toolsData: null });

		renderWithProviders(<MCP />);

		expect(screen.getByText("External MCP Access")).toBeInTheDocument();
		expect(
			screen.getByText(/could not load MCP tools/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /retry MCP tools/i }),
		).toBeEnabled();
		expect(
			screen.getByRole("combobox", { name: "Select Allowed Tools" }),
		).toBeDisabled();
	});

	it("retains changes after a failed save so the user can retry", async () => {
		saveMutation.mutateAsync.mockImplementationOnce(async () => {
			saveMutation.isError = true;
			throw new Error("offline");
		});
		const { user } = renderWithProviders(<MCP />);

		await user.click(
			screen.getByRole("switch", { name: "Enable MCP Access" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		await waitFor(() => expect(toast.error).toHaveBeenCalled());
		expect(saveMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				enabled: false,
				allowed_tool_ids: null,
				blocked_tool_ids: [],
			},
		});
		expect(
			screen.getByRole("switch", { name: "Enable MCP Access" }),
		).not.toBeChecked();
		expect(
			screen.getByText(/could not save MCP configuration/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Save Configuration" }),
		).toBeEnabled();
	});

	it("preserves a loaded empty allowed list when saving another change", async () => {
		mockQueries({
			configData: { ...config, allowed_tool_ids: [] },
		});
		const { user } = renderWithProviders(<MCP />);

		expect(
			screen.getByRole("switch", { name: "Allow All Tools" }),
		).not.toBeChecked();
		await user.click(
			screen.getByRole("switch", { name: "Enable MCP Access" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		expect(saveMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				enabled: false,
				allowed_tool_ids: [],
				blocked_tool_ids: [],
			},
		});
	});

	it("widens tool access only when Allow All Tools is explicitly enabled", async () => {
		mockQueries({
			configData: { ...config, allowed_tool_ids: ["workflow.run"] },
		});
		const { user } = renderWithProviders(<MCP />);

		const allowAll = screen.getByRole("switch", {
			name: "Allow All Tools",
		});
		expect(allowAll).not.toBeChecked();
		await user.click(allowAll);
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		expect(saveMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				enabled: true,
				allowed_tool_ids: null,
				blocked_tool_ids: [],
			},
		});
	});

	it("keeps an empty allowed list after removing the last selected tool", async () => {
		mockQueries({
			configData: { ...config, allowed_tool_ids: ["workflow.run"] },
		});
		const { user } = renderWithProviders(<MCP />);

		await user.click(
			screen.getByRole("button", {
				name: "Remove workflow.run from Allowed Tools",
			}),
		);
		expect(
			screen.getByRole("switch", { name: "Allow All Tools" }),
		).not.toBeChecked();
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		expect(saveMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				enabled: true,
				allowed_tool_ids: [],
				blocked_tool_ids: [],
			},
		});
	});

	it("keeps an empty allowed list after deselecting the last picker option", async () => {
		mockQueries({
			configData: { ...config, allowed_tool_ids: ["workflow.run"] },
		});
		const { user } = renderWithProviders(<MCP />);

		await user.click(
			screen.getByRole("combobox", { name: "Select Allowed Tools" }),
		);
		await user.click(screen.getByText("workflow.run"));
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		expect(saveMutation.mutateAsync).toHaveBeenCalledWith({
			body: {
				enabled: true,
				allowed_tool_ids: [],
				blocked_tool_ids: [],
			},
		});
	});

	it("keeps the reset dialog open and shows inline failure", async () => {
		deleteMutation.mutateAsync.mockImplementationOnce(async () => {
			deleteMutation.isError = true;
			throw new Error("offline");
		});
		const { user } = renderWithProviders(<MCP />);

		await user.click(
			screen.getByRole("button", { name: "Reset to Defaults" }),
		);
		const dialog = screen.getByRole("dialog", {
			name: "Reset MCP configuration?",
		});
		await user.click(
			screen.getByRole("button", { name: "Reset configuration" }),
		);

		await waitFor(() => expect(toast.error).toHaveBeenCalled());
		expect(dialog).toBeInTheDocument();
		expect(
			screen.getByText(/could not reset MCP configuration/i),
		).toBeInTheDocument();
	});
});
