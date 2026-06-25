import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { FilePolicyEditor } from "@/components/files/FilePolicyEditor";
import { PolicyRulesManager } from "@/components/policy-rules/PolicyRulesManager";
import {
	deleteFilePolicy,
	listFilePolicies,
	saveFilePolicy,
	type FilePolicy,
} from "@/services/filePolicies";
import { bestPolicyForPath, makeDefaultPolicy } from "./policyDraft";

interface PolicyEditorModalProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	location: string;
	scope: string | null;
	path: string;
	onSaved?: () => void;
}

export function PolicyEditorModal({
	open,
	onOpenChange,
	location,
	scope,
	path,
	onSaved,
}: PolicyEditorModalProps) {
	const [draft, setDraft] = useState<FilePolicy | null>(null);
	const [showRulesManager, setShowRulesManager] = useState(false);

	useEffect(() => {
		let cancelled = false;
		if (!open) return;
		listFilePolicies({ location, scope: scope ?? undefined })
			.then((result) => {
				if (cancelled) return;
				const best = bestPolicyForPath(result.policies ?? [], path, location);
				setDraft(best ?? makeDefaultPolicy(path, location, scope));
			})
			.catch(() => {
				if (!cancelled) setDraft(makeDefaultPolicy(path, location, scope));
			});
		return () => {
			cancelled = true;
		};
	}, [open, location, scope, path]);

	async function handleSave(policy: FilePolicy) {
		try {
			await saveFilePolicy(policy);
			toast.success("File policy saved");
			onSaved?.();
			onOpenChange(false);
		} catch (err) {
			toast.error("Failed to save file policy", {
				description: err instanceof Error ? err.message : String(err),
			});
		}
	}

	async function handleDelete(policy: FilePolicy) {
		try {
			await deleteFilePolicy(policy);
			toast.success("File policy deleted");
			onSaved?.();
			onOpenChange(false);
		} catch (err) {
			toast.error("Failed to delete file policy", {
				description: err instanceof Error ? err.message : String(err),
			});
		}
	}

	return (
		<>
			<Dialog open={open} onOpenChange={onOpenChange}>
				<DialogContent className="max-h-[90vh] gap-4 overflow-auto sm:max-w-2xl">
					<DialogHeader>
						<div className="flex items-center justify-between">
							<DialogTitle>Manage policy</DialogTitle>
							<Button
								type="button"
								size="sm"
								variant="ghost"
								className="text-xs"
								onClick={() => setShowRulesManager(true)}
								data-testid="manage-rules-btn"
							>
								Manage rules…
							</Button>
						</div>
					</DialogHeader>
					{draft && (
						<FilePolicyEditor
							key={`${draft.id ?? "draft"}:${draft.location}:${draft.organizationId ?? "global"}:${draft.path}`}
							path={path}
							value={draft}
							onSave={handleSave}
							onDelete={handleDelete}
						/>
					)}
				</DialogContent>
			</Dialog>

			<Dialog open={showRulesManager} onOpenChange={setShowRulesManager}>
				<DialogContent className="max-h-[90vh] overflow-auto sm:max-w-2xl">
					<DialogHeader>
						<DialogTitle>File policy rules</DialogTitle>
					</DialogHeader>
					<PolicyRulesManager domain="file" />
				</DialogContent>
			</Dialog>
		</>
	);
}
