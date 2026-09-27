import {
	createContext,
	useContext,
	useEffect,
	useRef,
	useState,
	type ReactNode,
} from "react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
const InstallContext = createContext({
	run: (action: () => void) => action(),
	finish: () => {},
	pending: false,
	setWide: (_wide: boolean) => {},
	setTall: (_tall: boolean) => {},
});
export const useInstallSession = () => useContext(InstallContext);
export function InstallSession({
	open,
	onClose,
	children,
	wide: initialWide = false,
}: {
	open: boolean;
	onClose: () => void;
	children: ReactNode;
	wide?: boolean;
}) {
	const busy = useRef(false);
	const [pending, setPending] = useState(false);
	const [wide, setWide] = useState(initialWide);
	const [tall, setTall] = useState(false);
	const focus = useDialogReturnFocus();
	const run = (action: () => void) => {
		if (busy.current) return;
		busy.current = true;
		setPending(true);
		action();
	};
	const finish = () => {
		busy.current = false;
		setPending(false);
	};
	return (
		<InstallContext.Provider value={{ run, finish, pending, setWide, setTall }}>
			<Dialog
				open={open}
				onOpenChange={(next) => {
					if (!next && !busy.current) onClose();
				}}
			>
				<DialogContent
					{...focus}
					showCloseButton={!pending}
					className={wide ? `flex min-w-0 max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl ${tall ? "h-[90dvh]" : ""}` : "max-h-[90dvh] overflow-y-auto sm:max-w-xl"}
					data-testid="solution-dialog"
				>
					<fieldset
						disabled={pending}
						className={wide ? "flex min-h-0 min-w-0 flex-1 flex-col" : "contents"}
						aria-busy={pending}
					>
						{children}
					</fieldset>
				</DialogContent>
			</Dialog>
		</InstallContext.Provider>
	);
}
export function InstallFailure({ message }: { message: string }) {
	const ref = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		ref.current?.focus();
		ref.current?.scrollIntoView({ block: "nearest" });
	}, [message]);
	return (
		<p
			ref={ref}
			role="alert"
			tabIndex={-1}
			className="rounded-[var(--bf-radius-control)] border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive [overflow-wrap:anywhere]"
		>
			{message}
		</p>
	);
}
