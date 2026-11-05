import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";

const api = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
const download = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ $api: api }));
vi.mock("@/services/bifrostRun", () => ({
	downloadBifrostRunPlugin: () => download(),
}));
vi.mock("@/components/LogoDropZone", () => ({
	LogoDropZone: (props: {
		uploadUrl: string;
		deleteUrl: string;
		ariaLabel: string;
	}) => (
		<div
			role="group"
			aria-label={props.ariaLabel}
			data-upload={props.uploadUrl}
			data-delete={props.deleteUrl}
		/>
	),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { MCPPackageBranding } from "./MCPPackageBranding";

const branding = {
	name: "Bifrost Agent",
	slug: "bifrost-agent",
	logo_url: null,
};
const refetch = vi.fn();
const mutateAsync = vi.fn();

beforeEach(() => {
	vi.clearAllMocks();
	api.useQuery.mockReturnValue({
		data: branding,
		isLoading: false,
		isError: false,
		refetch,
	});
	api.useMutation.mockReturnValue({ mutateAsync });
	mutateAsync.mockResolvedValue({
		name: "Acme Agent",
		slug: "acme-agent",
		logo_url: null,
	});
});

describe("MCPPackageBranding", () => {
	it("saves the package name independently and shows its generated identifier", async () => {
		const { user } = renderWithProviders(<MCPPackageBranding />);
		expect(screen.getByRole("heading", { name: "Agent Plugin Package", level: 2 })).toBeVisible();
		expect(screen.getByText("bifrost-agent")).toBeInTheDocument();
		await user.clear(screen.getByLabelText("Package Name"));
		await user.type(screen.getByLabelText("Package Name"), "Acme Agent");
		expect(
			screen.getByRole("button", { name: "Download Agent Plugin" }),
		).toBeDisabled();
		expect(screen.getByRole("status")).toHaveTextContent(
			/Save the package name before downloading/i,
		);
		await user.click(screen.getByRole("button", { name: "Save Package" }));
		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: { name: "Acme Agent" },
			}),
		);
	});

	it("keeps an invalid draft and explains how to fix it", async () => {
		const { user } = renderWithProviders(<MCPPackageBranding />);
		await user.clear(screen.getByLabelText("Package Name"));
		await user.type(screen.getByLabelText("Package Name"), "!!!");
		await user.click(screen.getByRole("button", { name: "Save Package" }));
		expect(screen.getByRole("alert")).toHaveTextContent(
			/letters, numbers/i,
		);
		expect(screen.getByLabelText("Package Name")).toHaveValue("!!!");
	});

	it("preserves the name draft when the server rejects a save", async () => {
		mutateAsync.mockRejectedValueOnce(new Error("Server unavailable"));
		const { user } = renderWithProviders(<MCPPackageBranding />);
		await user.clear(screen.getByLabelText("Package Name"));
		await user.type(
			screen.getByLabelText("Package Name"),
			"Covi Production",
		);
		await user.click(screen.getByRole("button", { name: "Save Package" }));
		await waitFor(() =>
			expect(screen.getByRole("alert")).toHaveTextContent(
				/Could not save/,
			),
		);
		expect(screen.getByLabelText("Package Name")).toHaveValue(
			"Covi Production",
		);
		expect(
			screen.getByRole("button", { name: "Save Package" }),
		).toBeEnabled();
	});

	it("connects the shared logo uploader to the package endpoints", () => {
		renderWithProviders(<MCPPackageBranding />);
		const uploader = screen.getByRole("group", {
			name: "Upload package logo",
		});
		expect(uploader).toHaveAttribute(
			"data-upload",
			"/api/mcp/run/branding/logo",
		);
		expect(uploader).toHaveAttribute(
			"data-delete",
			"/api/mcp/run/branding/logo",
		);
	});
});
