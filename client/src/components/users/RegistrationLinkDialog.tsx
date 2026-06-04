import { useEffect, useState } from "react";
import { Check, Copy, Info, Loader2, Mail } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { copyToClipboard } from "@/lib/clipboard";

interface RegistrationLinkDialogProps {
	open: boolean;
	email?: string;
	url?: string;
	canSendEmail: boolean;
	isSendingEmail?: boolean;
	onOpenChange: (open: boolean) => void;
	onSendEmail?: () => void | Promise<void>;
}

function absoluteRegistrationUrl(url: string): string {
	try {
		return new URL(url, window.location.origin).toString();
	} catch {
		return url;
	}
}

export function RegistrationLinkDialog({
	open,
	email,
	url,
	canSendEmail,
	isSendingEmail = false,
	onOpenChange,
	onSendEmail,
}: RegistrationLinkDialogProps) {
	const normalizedUrl = url ? absoluteRegistrationUrl(url) : "";
	const sendDisabled = !canSendEmail || !normalizedUrl || isSendingEmail;
	const [copied, setCopied] = useState(false);

	useEffect(() => {
		if (!copied) return;
		const timer = window.setTimeout(() => setCopied(false), 1600);
		return () => window.clearTimeout(timer);
	}, [copied]);

	const handleCopy = async () => {
		if (!normalizedUrl) return;
		if (await copyToClipboard(normalizedUrl)) {
			setCopied(true);
			toast.success("Registration link copied");
		} else {
			setCopied(false);
			toast.error("Failed to copy registration link");
		}
	};

	const handleSendEmail = async () => {
		if (sendDisabled || !onSendEmail) return;
		await onSendEmail();
	};

	const sendButton = (
		<Button
			type="button"
			className="w-full"
			onClick={handleSendEmail}
			disabled={sendDisabled}
		>
			{isSendingEmail ? (
				<Loader2 className="mr-2 h-4 w-4 animate-spin" />
			) : (
				<Mail className="mr-2 h-4 w-4" />
			)}
			Send Registration Email
			{!canSendEmail && <Info className="ml-2 h-4 w-4 opacity-80" />}
		</Button>
	);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader className="items-center text-center">
					<div className="mb-2 flex h-12 w-12 items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700">
						<Check className="h-6 w-6" />
					</div>
					<DialogTitle>User Created</DialogTitle>
					<DialogDescription>
						{email
							? `Send this to ${email} so they can finish logging in. They can also log in with SSO and register themselves automatically.`
							: "Send this to the user so they can finish logging in. They can also log in with SSO and register themselves automatically."}
					</DialogDescription>
				</DialogHeader>

				<div className="mt-2 flex flex-col gap-2">
					{canSendEmail ? (
						sendButton
					) : (
						<Tooltip>
							<TooltipTrigger asChild>
								<span
									tabIndex={0}
									aria-label="Registration email automation setup"
									className="inline-flex w-full cursor-help"
								>
									{sendButton}
								</span>
							</TooltipTrigger>
							<TooltipContent className="max-w-xs">
								Create an active{" "}
								<span className="font-mono">user.invited</span>{" "}
								event source with at least one subscription to
								enable registration emails.
							</TooltipContent>
						</Tooltip>
					)}

					<Button
						type="button"
						variant="outline"
						className="w-full"
						onClick={handleCopy}
						disabled={!normalizedUrl}
					>
						{copied ? (
							<Check className="mr-2 h-4 w-4 text-emerald-600" />
						) : (
							<Copy className="mr-2 h-4 w-4" />
						)}
						{copied ? "Copied" : "Copy Registration Link"}
					</Button>
				</div>
			</DialogContent>
		</Dialog>
	);
}
