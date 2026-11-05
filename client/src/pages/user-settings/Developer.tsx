import { useState, useCallback } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Code, Download, ExternalLink, Copy, Check } from "lucide-react";

import { copyToClipboard } from "@/lib/clipboard";
import { sdkService } from "@/services/sdk";

function SetupStep({
	number,
	title,
	command,
}: {
	number: number;
	title: string;
	command: string;
}) {
	const [copied, setCopied] = useState(false);
	const [copyError, setCopyError] = useState(false);

	const handleCopy = useCallback(async () => {
		setCopied(false);
		setCopyError(false);
		if (await copyToClipboard(command)) {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		} else {
			setCopyError(true);
		}
	}, [command]);

	return (
		<li className="min-w-0 space-y-2">
			<div className="flex items-center gap-2">
				<span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
					{number}
				</span>
				<p>{title}</p>
			</div>
			<div className="flex items-start gap-2 rounded-[var(--bf-radius-control)] bg-background/80 p-2">
				<code className="min-w-0 flex-1 self-center break-all text-xs">
					{command}
				</code>
				<button
					type="button"
					onClick={handleCopy}
					className="ml-auto flex min-h-11 min-w-11 flex-shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring text-muted-foreground transition-[color,background-color,box-shadow] duration-[var(--bf-motion-feedback)] hover:bg-muted hover:text-foreground sm:min-h-8 sm:min-w-8 motion-reduce:transition-none"
					title="Copy to clipboard"
					aria-label={copied ? "Copied" : "Copy to clipboard"}
				>
					{copied ? (
						<Check className="h-3.5 w-3.5" />
					) : (
						<Copy className="h-3.5 w-3.5" />
					)}
				</button>
			</div>
			{copyError && (
				<p role="alert" className="text-sm text-destructive">
					Could not copy. Select and copy the command above.
				</p>
			)}
		</li>
	);
}

export function DeveloperSettings() {
	const cliDownloadUrl = `${window.location.origin}${sdkService.getSdkDownloadUrl()}`;

	return (
		<div className="space-y-8">
			{/* SDK Setup Instructions */}
			<Card>
				<CardHeader>
					<div className="flex items-start gap-2">
						<Code className="h-5 w-5" />
						<CardTitle>
							Local Development with Bifrost SDK
						</CardTitle>
					</div>
					<CardDescription>
						Develop and test workflows locally using VS Code or your
						preferred IDE
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="space-y-3 rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/50 p-4">
						<p className="font-medium">Quick Start</p>
						<ol className="space-y-4 text-sm">
							<SetupStep
								number={1}
								title="Install the SDK:"
								command={`pipx install --force ${cliDownloadUrl}`}
							/>
							<SetupStep
								number={2}
								title="Login to authenticate:"
								command="bifrost login"
							/>
							<SetupStep
								number={3}
								title="Run your workflow:"
								command="bifrost run my_workflow.py"
							/>
						</ol>
					</div>

					<div className="flex flex-col gap-2 sm:flex-row">
						<Button variant="outline" asChild>
							<a href={sdkService.getSdkDownloadUrl()} download>
								<Download className="mr-2 h-4 w-4" />
								Download SDK
							</a>
						</Button>
						<Button variant="outline" asChild>
							<a
								href="https://gobifrost.com/docs/sdk-reference/sdk/external-sdk/"
								target="_blank"
								rel="noopener noreferrer"
							>
								<ExternalLink className="mr-2 h-4 w-4" />
								Documentation
							</a>
						</Button>
					</div>
				</CardContent>
			</Card>
		</div>
	);
}
