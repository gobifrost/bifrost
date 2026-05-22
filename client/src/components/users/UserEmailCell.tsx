import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * Truncating email cell with hover copy-to-clipboard.
 *
 * The inner `min-w-0` is required for `truncate` to work inside a flex row —
 * without it the text forces the cell wider and triggers horizontal scroll on
 * long emails (the Van Rooy seed row hit this).
 */
export function UserEmailCell({ email }: { email: string }) {
	const [copied, setCopied] = useState(false);

	const handleCopy = async (e: React.MouseEvent) => {
		e.stopPropagation();
		try {
			await navigator.clipboard.writeText(email);
			setCopied(true);
			toast.success("Email copied");
			window.setTimeout(() => setCopied(false), 1500);
		} catch {
			toast.error("Failed to copy email");
		}
	};

	return (
		<div className="flex items-center gap-1 min-w-0 group/email">
			<Tooltip>
				<TooltipTrigger asChild>
					<span className="block truncate min-w-0 flex-1">{email}</span>
				</TooltipTrigger>
				<TooltipContent>{email}</TooltipContent>
			</Tooltip>
			<Button
				variant="ghost"
				size="icon"
				className="h-6 w-6 shrink-0 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 transition-opacity"
				onClick={handleCopy}
				aria-label={`Copy ${email}`}
				title="Copy email"
			>
				{copied ? (
					<Check className="h-3.5 w-3.5" />
				) : (
					<Copy className="h-3.5 w-3.5" />
				)}
			</Button>
		</div>
	);
}
