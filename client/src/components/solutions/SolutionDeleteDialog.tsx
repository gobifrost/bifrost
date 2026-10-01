import { useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { getSolutionDeletionSummary } from "@/services/solutions";
import { InstallFailure } from "./InstallSession";
interface Props {
	id: string;
	name: string;
	slug: string;
	open: boolean;
	onClose: () => void;
	onDelete: (confirmation: string) => Promise<unknown>;
}
const counts = {
	files: "file",
	tables: "table",
	workflows: "workflow",
	apps: "app",
	forms: "form",
	agents: "agent",
	claims: "custom claim",
	events: "event",
	config_declarations: "config declaration",
} as const;
export function SolutionDeleteDialog(props: Props) {
	return props.open ? <DeleteSession key={props.id} {...props} /> : null;
}
function DeleteSession({ id, name, slug, onClose, onDelete }: Props) {
	const inputId = useId();
	const [confirmation, setConfirmation] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const busy = useRef(false);
	const focus = useDialogReturnFocus();
	const summary = useQuery({
		queryKey: ["solutions", id, "deletion-summary"],
		queryFn: () => getSolutionDeletionSummary(id),
		retry: false,
	});
	const remove = async () => {
		if (busy.current || confirmation !== slug) return;
		busy.current = true;
		setPending(true);
		setError(null);
		try {
			await onDelete(confirmation);
		} catch (cause) {
			setError(
				cause instanceof Error
					? cause.message
					: "Deletion failed. Try again.",
			);
		} finally {
			busy.current = false;
			setPending(false);
		}
	};
	return (
		<Dialog
			open
			onOpenChange={(next) => {
				if (!next && !busy.current) onClose();
			}}
		>
			<DialogContent
				{...focus}
				showCloseButton={!pending}
				data-testid="hard-delete-dialog"
				className="max-h-[90dvh] overflow-y-auto sm:max-w-xl"
			>
				<DialogHeader>
					<DialogTitle className="flex items-start gap-2">
						<Trash2
							aria-hidden="true"
							className="mt-1 size-4 shrink-0 text-destructive"
						/>
						<span>Permanently delete {name}?</span>
					</DialogTitle>
					<DialogDescription>
						This is irreversible. All owned entities and files will
						be permanently destroyed.
					</DialogDescription>
				</DialogHeader>
				{summary.isLoading ? (
					<p
						role="status"
						className="flex items-center gap-2 text-sm text-muted-foreground"
					>
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
						Loading what will be deleted…
					</p>
				) : summary.isError ? (
					<div className="space-y-2">
						<p
							role="alert"
							className="text-sm text-muted-foreground"
						>
							Could not load the deletion summary. Deletion still
							removes all owned content.
						</p>
						<Button
							variant="outline"
							disabled={pending || summary.isFetching}
							onClick={() => void summary.refetch()}
						>
							Retry deletion summary
						</Button>
					</div>
				) : summary.data ? (
					<div className="space-y-2">
						<ul
							data-testid="deletion-summary-list"
							className="grid grid-cols-2 gap-2 text-sm"
						>
							{(
								Object.keys(counts) as (keyof typeof counts)[]
							).map((key) => {
								const count = summary.data?.[key] ?? 0;
								return count > 0 ? (
									<li key={key}>
										{count} {counts[key]}
										{count === 1 ? "" : "s"}
									</li>
								) : null;
							})}
						</ul>
						{(summary.data.external_event_subscriptions ?? 0) >
						0 ? (
							<p
								data-testid="deletion-summary-external-listeners"
								className="text-sm text-destructive"
							>
								{summary.data.external_event_subscriptions}{" "}
								external listener
								{summary.data.external_event_subscriptions === 1
									? ""
									: "s"}{" "}
								outside this Solution will also be removed
								because they subscribe to its events or target
								its workflows or agents.
							</p>
						) : null}
					</div>
				) : null}
				<div className="space-y-2">
					<Label htmlFor={inputId}>
						Type the Solution slug to confirm
					</Label>
					<div
						data-testid="hard-delete-slug"
						className="break-all rounded-[var(--bf-radius-control)] border bg-muted px-3 py-2 font-mono text-xs"
					>
						{slug}
					</div>
					<Input
						id={inputId}
						data-testid="hard-delete-confirm-input"
						value={confirmation}
						onChange={(e) => setConfirmation(e.target.value)}
						disabled={pending}
						autoComplete="off"
						placeholder={slug}
					/>
				</div>
				{error && <InstallFailure message={error} />}
				<DialogFooter>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={pending}
						onClick={onClose}
					>
						Cancel
					</Button>
					<Button
						variant="destructive"
						className="min-h-11"
						data-testid="confirm-hard-delete"
						disabled={pending || confirmation !== slug}
						onClick={() => void remove()}
					>
						{pending ? (
							<Loader2
								aria-hidden="true"
								className="size-4 animate-spin motion-reduce:animate-none"
							/>
						) : (
							<Trash2 aria-hidden="true" className="size-4" />
						)}
						{error ? "Retry deletion" : "Delete permanently"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
