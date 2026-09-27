import { beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test-utils";

const useMediaQueryMock = vi.fn();
const useQueryMock = vi.fn();

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (...args: unknown[]) => useMediaQueryMock(...args),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => useQueryMock(...args),
	},
}));

vi.mock("@/components/mcp/MCPServerForm", () => ({
	MCPServerForm: () => <div data-testid="mcp-server-form" />,
}));

import { MCPServers } from "./MCPServers";

beforeEach(() => {
	useMediaQueryMock.mockReturnValue(false);
	useQueryMock.mockImplementation(
		(_method: string, path: string) => {
			if (path === "/api/mcp-servers") {
				return {
					data: [
						{
							id: "server-1",
							name: "Linear MCP",
							server_url: "https://linear.example/mcp",
							is_active: true,
							organization_id: null,
							created_at: "2026-01-01T00:00:00Z",
						},
					],
					isLoading: false,
					isError: false,
					isFetching: false,
					refetch: vi.fn(),
				};
			}
			return {
				data: [{ id: "connection-1", server_id: "server-1" }],
				isLoading: false,
				isError: false,
				isFetching: false,
				refetch: vi.fn(),
			};
		},
	);
});

describe("MCPServers", () => {
	it("opens a server row href on ctrl-click from a plain cell", () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);

		renderWithProviders(<MCPServers />);

		fireEvent.click(screen.getByRole("cell", { name: "1 org" }), {
			ctrlKey: true,
		});

		expect(open).toHaveBeenCalledWith("/mcp-servers/server-1", "_blank");
	});
});
