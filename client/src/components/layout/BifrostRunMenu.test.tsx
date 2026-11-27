import { beforeEach, describe, expect, it, vi } from "vitest";

import { act, renderWithProviders, screen } from "@/test-utils";

const useQueryMock = vi.fn();
const downloadPluginMock = vi.fn();
const copyToClipboardMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => useQueryMock(...args),
	},
}));

vi.mock("@/services/bifrostRun", () => ({
	downloadBifrostRunPlugin: () => downloadPluginMock(),
}));

vi.mock("@/lib/clipboard", () => ({
	copyToClipboard: (text: string) => copyToClipboardMock(text),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

import { BifrostRunMenu } from "./BifrostRunMenu";

const RUN_INFO = {
	enabled: true,
	mcp_url: "https://bifrost.example.com/mcp",
	setup_prompt:
		"Help me create a reusable skill or agent with this exact prompt:\n\nFind an agent, inspect its tools, then execute.",
};

describe("BifrostRunMenu", () => {
	beforeEach(() => {
		useQueryMock.mockReset();
		downloadPluginMock.mockReset();
		copyToClipboardMock.mockReset();
	});

	it("stays hidden while MCP is disabled", () => {
		useQueryMock.mockReturnValue({ data: { ...RUN_INFO, enabled: false } });

		const { container } = renderWithProviders(
			<BifrostRunMenu open onOpenChange={vi.fn()} />,
		);

		expect(container.firstChild).toBeNull();
	});

	it("explains portable and manual setup paths", async () => {
		useQueryMock.mockReturnValue({ data: RUN_INFO });
		renderWithProviders(
			<BifrostRunMenu open onOpenChange={vi.fn()} />,
		);

		expect(screen.getByText("Use Bifrost with AI")).toBeInTheDocument();
		expect(screen.getByText(/Claude Code, Codex/)).toBeInTheDocument();
		expect(
			screen.getByText(/Claude Desktop, Microsoft Copilot/),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Manual Setup", level: 3 }),
		).toBeInTheDocument();
		expect(screen.getByText(RUN_INFO.mcp_url)).toBeInTheDocument();
		expect(
			screen.getByText("1. Connect the MCP server"),
		).toBeInTheDocument();
		expect(
			screen.getByText("2. Add the Bifrost behavior"),
		).toBeInTheDocument();
	});

	it("copies the MCP URL and canonical instructions through the shared helper", async () => {
		useQueryMock.mockReturnValue({ data: RUN_INFO });
		copyToClipboardMock
			.mockResolvedValueOnce(false)
			.mockResolvedValueOnce(true)
			.mockResolvedValueOnce(true);
		const { user } = renderWithProviders(
			<BifrostRunMenu open onOpenChange={vi.fn()} />,
		);

		await user.click(screen.getByRole("button", { name: "Copy MCP URL" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not copy to the clipboard. Try again.",
		);
		await user.click(
			screen.getByRole("button", { name: "Copy setup prompt" }),
		);
		await user.click(screen.getByRole("button", { name: "Copy MCP URL" }));

		expect(copyToClipboardMock).toHaveBeenNthCalledWith(
			1,
			RUN_INFO.mcp_url,
		);
		expect(copyToClipboardMock).toHaveBeenNthCalledWith(
			2,
			RUN_INFO.setup_prompt,
		);
		expect(copyToClipboardMock).toHaveBeenNthCalledWith(
			3,
			RUN_INFO.mcp_url,
		);
		expect(screen.getByRole("status")).toHaveTextContent("MCP URL copied");
	});

	it("downloads the instance-generated plugin", async () => {
		useQueryMock.mockReturnValue({ data: RUN_INFO });
		downloadPluginMock.mockResolvedValue({
			blob: new Blob(["plugin"]),
			filename: "bifrost-agent.zip",
		});
		const createObjectURL = vi
			.spyOn(URL, "createObjectURL")
			.mockReturnValue("blob:bifrost-run");
		const revokeObjectURL = vi
			.spyOn(URL, "revokeObjectURL")
			.mockImplementation(() => undefined);
		const click = vi
			.spyOn(HTMLAnchorElement.prototype, "click")
			.mockImplementation(() => undefined);
		const { user } = renderWithProviders(
			<BifrostRunMenu open onOpenChange={vi.fn()} />,
		);

		await user.click(
			screen.getByRole("button", { name: "Download Agent Plugin" }),
		);

		expect(downloadPluginMock).toHaveBeenCalledOnce();
		expect(createObjectURL).toHaveBeenCalledOnce();
		expect(click).toHaveBeenCalledOnce();
		expect(revokeObjectURL).toHaveBeenCalledWith("blob:bifrost-run");

		click.mockRestore();
		createObjectURL.mockRestore();
		revokeObjectURL.mockRestore();
	});

	it("prevents repeated downloads while pending and retains a retryable failure", async () => {
		useQueryMock.mockReturnValue({ data: RUN_INFO });
		let reject!: (error: Error) => void;
		downloadPluginMock.mockImplementationOnce(
			() =>
				new Promise((_, fail) => {
					reject = fail;
				}),
		);
		const { user } = renderWithProviders(
			<BifrostRunMenu open onOpenChange={vi.fn()} />,
		);
		await user.click(
			screen.getByRole("button", { name: "Download Agent Plugin" }),
		);
		expect(
			screen.getByRole("button", { name: "Downloading…" }),
		).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Downloading…" }));
		expect(downloadPluginMock).toHaveBeenCalledTimes(1);
		await act(async () => reject(new Error("network")));
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Could not download Bifrost Agent. Try again.",
		);
		expect(
			screen.getByRole("button", { name: "Download Agent Plugin" }),
		).toBeEnabled();
	});
});
