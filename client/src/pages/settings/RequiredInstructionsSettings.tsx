import { useEffect, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
} from "@/components/ui/card";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import {
	getRequiredInstructionsSettings,
	updateRequiredInstructionsSettings,
} from "@/services/required-instructions";

interface RequiredInstructionsSettingsProps {
	organizationId?: string;
	embedded?: boolean;
}

export function RequiredInstructionsSettings({
	organizationId,
	embedded = false,
}: RequiredInstructionsSettingsProps) {
	const organizationScoped = Boolean(organizationId);
	const title = organizationScoped
		? "Organization Instructions"
		: "Global Instructions";
	const [instructions, setInstructions] = useState("");
	const [savedInstructions, setSavedInstructions] = useState("");
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		let active = true;
		getRequiredInstructionsSettings(organizationId)
			.then((settings) => {
				if (!active) return;
				setInstructions(settings.instructions);
				setSavedInstructions(settings.instructions);
			})
			.catch(() => toast.error(`Failed to load ${title.toLowerCase()}`))
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, [organizationId, title]);

	const handleSave = async () => {
		setSaving(true);
		try {
			const settings = await updateRequiredInstructionsSettings(
				instructions,
				organizationId,
			);
			setInstructions(settings.instructions);
			setSavedInstructions(settings.instructions);
			toast.success(`${title} saved`);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: `Failed to save ${title.toLowerCase()}`,
			);
		} finally {
			setSaving(false);
		}
	};

	const content = (
		<>
			<div className="space-y-1">
				<div className="flex items-center gap-2">
					<FileText className="h-5 w-5" />
					<h3 className="font-semibold leading-none tracking-tight">
						{title}
					</h3>
				</div>
				<p className="text-sm text-muted-foreground">
					{organizationScoped
						? "Applied after global instructions for members of this organization."
						: "Applied to every task performed through the default Bifrost MCP endpoint."}
				</p>
			</div>
			{loading ? (
				<div className="flex min-h-[200px] items-center justify-center rounded-md border">
					<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
				</div>
			) : (
				<TiptapEditor
					content={instructions}
					onChange={setInstructions}
					placeholder="Add instructions in Markdown..."
					ariaLabel={`${title} editor`}
					editorClassName="min-h-[220px]"
				/>
			)}
			<div className="flex justify-end">
				<Button
					onClick={handleSave}
					disabled={loading || saving || instructions === savedInstructions}
				>
					{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
					Save Instructions
				</Button>
			</div>
		</>
	);

	if (embedded) {
		return <div className="space-y-4">{content}</div>;
	}

	return (
		<Card>
			<CardContent className="space-y-4 pt-6">{content}</CardContent>
		</Card>
	);
}
