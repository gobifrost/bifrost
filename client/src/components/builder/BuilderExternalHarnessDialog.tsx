import { useState } from "react";
import { Check, Copy, PlugZap, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import type { BuilderSession } from "@/services/builder";

function CopyValue({ label, value }: { label: string; value: string }) {
	const [copied, setCopied] = useState(false);

	async function copy() {
		try {
			await navigator.clipboard.writeText(value);
			setCopied(true);
			toast.success(`${label} copied`);
			window.setTimeout(() => setCopied(false), 1_500);
		} catch {
			toast.error(`Could not copy ${label.toLowerCase()}`);
		}
	}

	return (
		<div className="space-y-1.5">
			<p className="text-xs font-medium text-foreground">{label}</p>
			<div className="flex items-center gap-2 rounded-lg border bg-muted/35 p-2">
				<code className="min-w-0 flex-1 break-all text-xs text-foreground">
					{value}
				</code>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="h-8 w-8 shrink-0"
					aria-label={`Copy ${label}`}
					onClick={() => void copy()}
				>
					{copied ? (
						<Check className="h-4 w-4 text-emerald-600" />
					) : (
						<Copy className="h-4 w-4" />
					)}
				</Button>
			</div>
		</div>
	);
}

export function BuilderExternalHarnessDialog({
	session,
	targetKind = "solution",
}: {
	session: BuilderSession | undefined;
	targetKind?: "solution" | "organization" | "global_repo";
}) {
	const mcpUrl = `${window.location.origin}/mcp`;
	const starterPrompt = session
		? targetKind === "organization"
			? `Continue Bifrost Builder session ${session.id}. Discover its session-bound organization tools and use only those tools; Bifrost has already fixed the target organization and will enforce my current permissions on every call.`
			: `Continue Bifrost Builder session ${session.id} for Solution ${session.solution_id}. Discover the session-bound Builder workspace tools, keep finalize=false for intermediate edits, and set finalize=true only on the final successful mutation.`
		: "";
	const isOrganizationWorkspace = targetKind === "organization";

	return (
		<Dialog>
			<DialogTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					disabled={!session}
					title={
						session
							? "Continue this build from your own AI harness"
							: "Start a Builder session first"
					}
				>
					<PlugZap className="h-4 w-4" />
					<span className="hidden xl:inline">Use your AI</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="max-w-xl">
				<DialogHeader>
					<div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
						<Wrench className="h-5 w-5" />
					</div>
					<DialogTitle>Continue with your own AI harness</DialogTitle>
					<DialogDescription>
						{isOrganizationWorkspace
							? "Connect Codex, Claude Code, or another MCP client to the same organization workspace. Bifrost exposes only your authorized tools and rechecks every change."
							: "Connect Codex, Claude Code, or another MCP client to the same private workspace. Bifrost still enforces access, records every revision, validates the Solution, and runs the final build."}
					</DialogDescription>
				</DialogHeader>

				{session ? (
					<div className="space-y-5">
						<div className="space-y-3">
							<CopyValue label="MCP server" value={mcpUrl} />
							<CopyValue label="Build ID" value={session.solution_id} />
							<CopyValue label="Builder session ID" value={session.id} />
						</div>

						<div className="rounded-xl border bg-muted/20 p-4">
							<p className="text-sm font-medium">What your assistant should do</p>
							<ol className="mt-2 list-decimal space-y-1.5 pl-4 text-sm text-muted-foreground">
								<li>Connect to the MCP server and sign in as you.</li>
								<li>Discover the Builder workspace tools available to your account.</li>
								<li>Pass the Builder session ID to every tool call.</li>
								{isOrganizationWorkspace ? (
									<li>Use only the discovered organization tools; each accepted change is live.</li>
								) : (
									<li>Finalize only the last edit to enqueue one app build.</li>
								)}
							</ol>
						</div>

						<CopyValue label="Starter prompt" value={starterPrompt} />
						<p className="text-xs leading-5 text-muted-foreground">
							{isOrganizationWorkspace
								? "The selected organization and your permissions stay bound to this session. The external assistant's own transcript remains in that assistant."
								: "Source and revision history stay shared with this screen. The external assistant's own chat transcript remains in that assistant."}
						</p>
					</div>
				) : null}
			</DialogContent>
		</Dialog>
	);
}
