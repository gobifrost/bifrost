import { useRef, useState } from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/lib/clipboard";

export function HmacSecretReveal({ value, onDismiss, label = "New embed secret" }: { value: string; onDismiss: () => void; label?: string }) {
	const [state, setState] = useState<"idle" | "copying" | "copied" | "error">("idle");
	const copying = useRef(false);
	const copy = async () => {
		if (copying.current) return;
		copying.current = true;
		setState("copying");
		try { setState(await copyToClipboard(value) ? "copied" : "error"); }
		catch { setState("error"); }
		finally { copying.current = false; }
	};
	return (
		<section aria-label={label} className="min-w-0 space-y-4 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)] bg-[var(--bf-warning-soft)] p-[var(--bf-surface-pad)]">
			<p className="text-sm font-medium">Copy this secret now — it will not be shown again.</p>
			<code tabIndex={0} onFocus={event => { const selection = window.getSelection(); const range = document.createRange(); range.selectNodeContents(event.currentTarget); selection?.removeAllRanges(); selection?.addRange(range); }} aria-label="Secret value" className="block whitespace-pre-wrap rounded-[var(--bf-radius-control)] border bg-background p-3 font-mono text-sm select-all [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-ring">{value}</code>
			{state === "error" && <p role="alert" className="text-sm">Could not copy the secret. Select the value and copy it manually, or try again.</p>}
			{state === "copied" && <p role="status" className="text-sm">Secret copied</p>}
			<div className="flex flex-wrap gap-2">
				<Button type="button" variant="outline" className="min-h-11" disabled={state === "copying"} onClick={() => void copy()}><Copy aria-hidden="true" className="size-4" />{state === "copying" ? "Copying…" : "Copy secret"}</Button>
				<Button type="button" variant="outline" className="min-h-11" disabled={state === "copying"} onClick={onDismiss}>Dismiss secret</Button>
			</div>
		</section>
	);
}
