import { Bot, Copy, Download } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { $api } from "@/lib/api-client";
import { downloadBifrostRunPlugin } from "@/services/bifrostRun";

async function copyText(value: string, successMessage: string) {
	try {
		await navigator.clipboard.writeText(value);
		toast.success(successMessage);
	} catch {
		toast.error("Could not copy to the clipboard");
	}
}

export function BifrostRunMenu() {
	const { data: info } = $api.useQuery("get", "/api/mcp/run", undefined, {
		refetchInterval: 60_000,
	});

	if (!info?.enabled) return null;

	const handleDownload = async () => {
		try {
			const { blob, filename } = await downloadBifrostRunPlugin();
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = filename;
			link.click();
			URL.revokeObjectURL(url);
			toast.success("Bifrost Agent downloaded");
		} catch {
			toast.error("Could not download Bifrost Agent");
		}
	};

	return (
		<div className="mr-1 sm:mr-2">
			<Popover>
				<PopoverTrigger asChild>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Connect AI assistants"
						title="Connect AI assistants"
					>
						<Bot className="h-4 w-4" />
					</Button>
				</PopoverTrigger>
				<PopoverContent
					className="w-[calc(100vw-2rem)] p-0 sm:w-96"
					align="end"
					sideOffset={8}
				>
					<div className="border-b p-4">
						<div className="flex items-start gap-3">
							<div className="rounded-lg bg-primary/10 p-2 text-primary">
								<Bot className="h-5 w-5" />
							</div>
							<div className="min-w-0">
								<h2 className="font-semibold">
									Use Bifrost with AI
								</h2>
								<p className="mt-1 text-sm text-muted-foreground">
									Connect one AI assistant to all agents and
									tools available in this Bifrost instance.
								</p>
							</div>
						</div>
					</div>

					<div className="space-y-5 p-4">
						<section className="space-y-2">
							<div>
								<p className="text-sm font-medium">
									Agent Plugin
								</p>
								<p className="text-xs text-muted-foreground">
									For Claude Code, Codex, GitHub Copilot,
									Cursor, Gemini CLI, and compatible clients.
								</p>
							</div>
							<Button
								className="w-full"
								onClick={() => void handleDownload()}
							>
								<Download className="mr-2 h-4 w-4" />
								Download Agent Plugin
							</Button>
						</section>

						<div className="border-t" />

						<section className="space-y-4">
							<div>
								<h3 className="text-base font-semibold">
									Manual Setup
								</h3>
								<p className="text-xs text-muted-foreground">
									For Claude Desktop, Microsoft Copilot
									Studio, and clients that cannot import the
									plugin.
								</p>
							</div>
							<div className="space-y-2">
								<div className="text-sm font-medium">
									1. Connect the MCP server
								</div>
								<p className="text-xs text-muted-foreground">
									Add this as a Streamable HTTP MCP server.
									Setup varies by provider.
								</p>
								<div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2">
									<code className="min-w-0 flex-1 break-all text-xs">
										{info.mcp_url}
									</code>
									<Button
										variant="ghost"
										size="icon"
										className="h-8 w-8 shrink-0"
										aria-label="Copy MCP URL"
										title="Copy MCP URL"
										onClick={() =>
											void copyText(
												info.mcp_url,
												"MCP URL copied",
											)
										}
									>
										<Copy className="h-4 w-4" />
									</Button>
								</div>
							</div>
							<div className="space-y-2">
								<p className="text-sm font-medium">
									2. Add the Bifrost behavior
								</p>
								<p className="text-xs text-muted-foreground">
									Paste this prompt into your AI service to
									create a reusable skill or agent.
								</p>
								<Button
									variant="outline"
									size="sm"
									className="w-full"
									onClick={() =>
										void copyText(
											info.setup_prompt,
											"Setup prompt copied",
										)
									}
								>
									<Copy className="mr-2 h-4 w-4" />
									Copy setup prompt
								</Button>
							</div>
						</section>
					</div>
				</PopoverContent>
			</Popover>
		</div>
	);
}
