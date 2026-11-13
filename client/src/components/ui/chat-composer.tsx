import { Send, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ChatComposerProps {
	placeholder?: string;
	onSend: (text: string) => void | Promise<void>;
	pending?: boolean;
	className?: string;
	autoFocus?: boolean;
}

export function ChatComposer({
	placeholder = "Type a message...",
	onSend,
	pending = false,
	className,
	autoFocus,
}: ChatComposerProps) {
	const [value, setValue] = useState("");

	const sendingRef = useRef(false);
	const inputRef = useRef<HTMLTextAreaElement>(null);
	const [sending, setSending] = useState(false);
	const [failed, setFailed] = useState(false);
	const busy = pending || sending;
	async function submit() {
		if (!value.trim() || pending || sendingRef.current) return;
		sendingRef.current = true;
		setSending(true);
		setFailed(false);
		try {
			await onSend(value.trim());
			setValue("");
		} catch {
			setFailed(true);
			inputRef.current?.focus();
		} finally {
			sendingRef.current = false;
			setSending(false);
		}
	}

	return (
		<div
			className={cn(
				"flex min-w-0 flex-wrap items-end gap-2 rounded-[var(--bf-radius-control)] border border-input bg-background p-2 transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none",
				"focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20",
				className,
			)}
		>
			<textarea
				ref={inputRef}
				readOnly={busy}
				aria-label={placeholder}
				autoFocus={autoFocus}
				placeholder={placeholder}
				value={value}
				onChange={(e) => {
					setValue(e.target.value);
					setFailed(false);
				}}
				onKeyDown={(e) => {
					if (
						e.key === "Enter" &&
						!e.shiftKey &&
						!e.nativeEvent.isComposing &&
						e.nativeEvent.keyCode !== 229
					) {
						e.preventDefault();
						void submit();
					}
				}}
				className="min-w-0 flex-1 field-sizing-content resize-none overflow-y-auto border-none bg-transparent px-1 py-2 text-base sm:text-sm leading-6 outline-none placeholder:text-muted-foreground min-h-11 max-h-[180px] [overflow-wrap:anywhere]"
				rows={2}
			/>
			<Button
				type="button"
				size="icon"
				onClick={submit}
				disabled={!value.trim() || busy}
				aria-label={busy ? "Sending" : "Send"}
				className="size-11 shrink-0"
			>
				{busy ? (
					<Loader2 className="size-4 motion-safe:animate-spin" />
				) : (
					<Send className="size-4" />
				)}
			</Button>
			{failed ? (
				<p
					role="alert"
					className="w-full text-sm text-[var(--bf-danger)]"
				>
					Message could not be sent. Your text is still here; try
					sending again.
				</p>
			) : null}
		</div>
	);
}
