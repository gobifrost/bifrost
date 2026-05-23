/**
 * Shared help slide-out: a HelpCircle icon button that opens a right-side
 * sheet rendering arbitrary reference content. Extracted from the
 * PolicyReferencePanel so future schema-driven editors (Custom Claims, etc.)
 * can reuse the same chrome.
 *
 * The component is self-contained — it owns the open/close state and the
 * trigger. Consumers just pass a title and children (the body content).
 */

import { useState, type ReactNode } from "react";
import { HelpCircle } from "lucide-react";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface HelpSlideoutProps {
	title: string;
	children: ReactNode;
	/** Optional className for the trigger button (icon size, etc.). */
	triggerClassName?: string;
}

export function HelpSlideout({
	title,
	children,
	triggerClassName,
}: HelpSlideoutProps) {
	const [open, setOpen] = useState(false);
	return (
		<Sheet open={open} onOpenChange={setOpen}>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				aria-label={title}
				className={cn("h-8 w-8 p-0", triggerClassName)}
				onClick={() => setOpen(true)}
			>
				<HelpCircle className="h-4 w-4" />
			</Button>
			<SheetContent
				side="right"
				className="w-[420px] sm:w-[480px] overflow-y-auto"
				aria-label={title}
			>
				<SheetHeader>
					<SheetTitle>{title}</SheetTitle>
				</SheetHeader>
				<div className="space-y-6 px-4 pb-6">{children}</div>
			</SheetContent>
		</Sheet>
	);
}
