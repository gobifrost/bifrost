import { useState, useCallback } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
	Code,
	Download,
	ExternalLink,
	Copy,
	Check,
} from "lucide-react";
import { toast } from "sonner";

import { copyToClipboard } from "@/lib/clipboard";
import { sdkService } from "@/services/sdk";

function CopyButton({ text }: { text: string }) {
	const [copied, setCopied] = useState(false);

	const handleCopy = useCallback(async () => {
		if (await copyToClipboard(text)) {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		} else {
			toast.error("Failed to copy to clipboard");
		}
	}, [text]);

	return (
		<button
			type="button"
			onClick={handleCopy}
			className="ml-auto flex-shrink-0 p-1 rounded-md hover:bg-muted-foreground/20 text-muted-foreground hover:text-foreground transition-colors"
			title="Copy to clipboard"
		>
			{copied ? (
				<Check className="h-3.5 w-3.5" />
			) : (
				<Copy className="h-3.5 w-3.5" />
			)}
		</button>
	);
}

export function DeveloperSettings() {
	return (
		<div className="space-y-6">
			{/* SDK Setup Instructions */}
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
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
					<div className="rounded-lg bg-muted/50 p-4 space-y-3 ring-1 ring-foreground/5">
						<p className="font-medium">Quick Start</p>
						<div className="space-y-2 text-sm">
							<div className="flex items-start gap-2">
								<span className="bg-primary text-primary-foreground rounded-full w-5 h-5 flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
									1
								</span>
								<div className="flex-1">
									<p>Install the SDK:</p>
									<code className="flex items-center mt-1 p-2 bg-muted rounded-md text-xs">
										<span>pipx install --force {window.location.origin}/api/cli/download</span>
										<CopyButton text={`pipx install --force ${window.location.origin}/api/cli/download`} />
									</code>
								</div>
							</div>
							<div className="flex items-start gap-2">
								<span className="bg-primary text-primary-foreground rounded-full w-5 h-5 flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
									2
								</span>
								<div className="flex-1">
									<p>Login to authenticate:</p>
									<code className="flex items-center mt-1 p-2 bg-muted rounded-md text-xs">
										<span>bifrost login</span>
										<CopyButton text="bifrost login" />
									</code>
								</div>
							</div>
							<div className="flex items-start gap-2">
								<span className="bg-primary text-primary-foreground rounded-full w-5 h-5 flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
									3
								</span>
								<div className="flex-1">
									<p>Run your workflow:</p>
									<code className="flex items-center mt-1 p-2 bg-muted rounded-md text-xs">
										<span>bifrost run my_workflow.py</span>
										<CopyButton text="bifrost run my_workflow.py" />
									</code>
								</div>
							</div>
						</div>
					</div>

					<div className="flex gap-2">
						<Button variant="outline" asChild>
							<a href={sdkService.getSdkDownloadUrl()} download>
								<Download className="h-4 w-4 mr-2" />
								Download SDK
							</a>
						</Button>
						<Button variant="outline" asChild>
							<a
								href="https://docs.gobifrost.com/sdk"
								target="_blank"
								rel="noopener noreferrer"
							>
								<ExternalLink className="h-4 w-4 mr-2" />
								Documentation
							</a>
						</Button>
					</div>
				</CardContent>
			</Card>

		</div>
	);
}
