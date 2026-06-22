import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { saveFilePolicy } from "@/services/filePolicies";

interface NewShareDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	scope: string | null;
	onCreated: (location: string) => void;
}

// Reserved/blocked names the explorer never creates as shares.
const RESERVED = new Set(["workspace", "uploads", "temp", "_repo", "_tmp", "_apps"]);
const NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

export function NewShareDialog({
	open,
	onOpenChange,
	scope,
	onCreated,
}: NewShareDialogProps) {
	const [name, setName] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);

	function validate(value: string): string | null {
		const trimmed = value.trim();
		if (!trimmed) return "Enter a share name.";
		if (RESERVED.has(trimmed)) return `'${trimmed}' is a reserved name.`;
		if (!NAME_PATTERN.test(trimmed))
			return "Use lowercase letters, numbers, and hyphens.";
		return null;
	}

	async function handleCreate() {
		const trimmed = name.trim();
		const validationError = validate(trimmed);
		if (validationError) {
			setError(validationError);
			return;
		}
		setSaving(true);
		setError(null);
		try {
			// Create the first policy (empty doc → backend seeds admin_bypass).
			await saveFilePolicy({
				location: trimmed,
				path: "",
				organizationId: scope,
				policies: { policies: [] },
			});
			toast.success(`Share '${trimmed}' created`);
			onCreated(trimmed);
			onOpenChange(false);
			setName("");
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setSaving(false);
		}
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>New share</DialogTitle>
				</DialogHeader>
				<div className="space-y-1">
					<Label htmlFor="new-share-name">Share name</Label>
					<Input
						id="new-share-name"
						value={name}
						placeholder="reports"
						onChange={(event) => {
							setName(event.target.value);
							setError(null);
						}}
					/>
					{error && <p className="text-xs text-destructive">{error}</p>}
					<p className="text-xs text-muted-foreground">
						Creates an admin-managed policy so you can grant access. Files land
						under <span className="font-mono">{name.trim() || "name"}/</span>.
					</p>
				</div>
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button type="button" onClick={() => void handleCreate()} disabled={saving}>
						Create share
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
