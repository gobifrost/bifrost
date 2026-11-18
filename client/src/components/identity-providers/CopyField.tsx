import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { copyToClipboard } from "@/lib/clipboard";

/** A read-only value with a copy button, for values pasted into another system. */
export function CopyField({
	id,
	value,
	label,
}: {
	id: string;
	value: string;
	label: string;
}) {
	const [copied, setCopied] = useState(false);
	const copy = async () => {
		if (await copyToClipboard(value)) {
			setCopied(true);
			window.setTimeout(() => setCopied(false), 2000);
		}
	};
	return (
		<div className="flex min-w-0 gap-2">
			<Input
				id={id}
				aria-label={label}
				readOnly
				value={value}
				className="min-w-0"
				onFocus={(event) => event.currentTarget.select()}
			/>
			<Button
				type="button"
				variant="outline"
				size="icon"
				className="h-9 w-9 shrink-0"
				aria-label={`Copy ${label}`}
				title={`Copy ${label}`}
				onClick={() => void copy()}
			>
				{copied ? (
					<Check aria-hidden="true" className="h-4 w-4" />
				) : (
					<Copy aria-hidden="true" className="h-4 w-4" />
				)}
			</Button>
		</div>
	);
}
