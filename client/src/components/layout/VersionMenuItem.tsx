import { useEffect, useRef, useState } from "react";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { copyToClipboard } from "@/lib/clipboard";
import { APP_VERSION } from "@/lib/version";

export function VersionMenuItem() {
	const [status, setStatus] = useState<
		"idle" | "copying" | "copied" | "failed"
	>("idle");
	const mounted = useRef(true);
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	useEffect(() => {
		if (status !== "copied") return;
		const timer = setTimeout(() => setStatus("idle"), 1500);
		return () => clearTimeout(timer);
	}, [status]);
	return (
		<DropdownMenuItem
			className="min-h-11 justify-center whitespace-normal text-center font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]"
			aria-label={`Copy version ${APP_VERSION}`}
			disabled={status === "copying"}
			onSelect={(event) => {
				event.preventDefault();
				setStatus("copying");
				void copyToClipboard(APP_VERSION).then((success) => {
					if (mounted.current)
						setStatus(success ? "copied" : "failed");
				});
			}}
		>
			<span aria-live="polite">
				{status === "copying"
					? "Copying…"
					: status === "copied"
						? "Copied!"
						: status === "failed"
							? "Copy failed. Try again."
							: APP_VERSION}
			</span>
		</DropdownMenuItem>
	);
}
