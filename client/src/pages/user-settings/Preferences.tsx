import { useEffect, useState } from "react";
import { Brain, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { TiptapEditor } from "@/components/ui/tiptap-editor";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
	getUserMemorySettings,
	listMemories,
	removeMemory,
	updateUserMemorySettings,
	type MemoryEntry,
	type MemoryUserSettings,
} from "@/services/memory";

export function Preferences() {
	const [settings, setSettings] = useState<MemoryUserSettings | null>(null);
	const [memories, setMemories] = useState<MemoryEntry[]>([]);
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [deleting, setDeleting] = useState(false);
	const [selectedMemory, setSelectedMemory] = useState<MemoryEntry | null>(
		null,
	);

	useEffect(() => {
		let active = true;
		Promise.all([getUserMemorySettings(), listMemories()])
			.then(([nextSettings, memoryList]) => {
				if (!active) return;
				setSettings(nextSettings);
				setMemories(memoryList.entries);
			})
			.catch(() => toast.error("Failed to load memory preferences"))
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, []);

	const handleToggle = async (enabled: boolean) => {
		setSaving(true);
		try {
			const nextSettings = await updateUserMemorySettings(enabled);
			setSettings(nextSettings);
			toast.success(enabled ? "Memory enabled" : "Memory disabled");
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update memory preference",
			);
		} finally {
			setSaving(false);
		}
	};

	const handleDelete = async () => {
		if (!selectedMemory) return;
		setDeleting(true);
		try {
			await removeMemory(selectedMemory.id);
			setMemories((current) =>
				current.filter((memory) => memory.id !== selectedMemory.id),
			);
			setSelectedMemory(null);
			toast.success("Memory removed");
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to remove memory",
			);
		} finally {
			setDeleting(false);
		}
	};

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Brain className="h-5 w-5" />
						<CardTitle>Memory</CardTitle>
					</div>
					<CardDescription>
						Let Bifrost-connected AI assistants use durable, private
						context that you explicitly ask them to remember.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="flex items-center justify-between gap-6">
						<div className="space-y-1">
							<Label htmlFor="user-memory-enabled">
								Enable Memory
							</Label>
							<p className="text-sm text-muted-foreground">
								{settings?.platform_enabled === false
									? "Memory is currently disabled by your platform administrator."
									: "Only your account can search or manage these memories."}
							</p>
						</div>
						<div className="flex items-center gap-2">
							{(loading || saving) && (
								<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
							)}
							<Switch
								id="user-memory-enabled"
								checked={settings?.user_enabled ?? false}
								disabled={
									loading ||
									saving ||
									settings?.platform_enabled === false
								}
								onCheckedChange={handleToggle}
							/>
						</div>
					</div>
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<CardTitle>Saved Memories</CardTitle>
					<CardDescription>
						Review or remove anything Bifrost has remembered for
						you.
					</CardDescription>
				</CardHeader>
				<CardContent>
					{loading ? (
						<div className="flex items-center gap-2 text-sm text-muted-foreground">
							<Loader2 className="h-4 w-4 animate-spin" />
							Loading memories…
						</div>
					) : memories.length === 0 ? (
						<div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
							Nothing has been remembered yet.
						</div>
					) : (
						<div className="max-h-[32rem] space-y-3 overflow-auto pr-1">
							{memories.map((memory) => (
								<div
									key={memory.id}
									className="rounded-lg border p-4"
								>
									<div className="flex items-start justify-between gap-4">
										<div className="min-w-0 flex-1">
											<TiptapEditor
												content={memory.content}
												readOnly
												ariaLabel="Saved memory"
												className="border-0"
												editorClassName="min-h-0 p-0"
											/>
											<p className="mt-3 text-xs text-muted-foreground">
												Remembered{" "}
												{new Date(
													memory.created_at,
												).toLocaleString()}
											</p>
										</div>
										<Button
											variant="ghost"
											size="icon"
											aria-label="Remove memory"
											onClick={() =>
												setSelectedMemory(memory)
											}
										>
											<Trash2 className="h-4 w-4" />
										</Button>
									</div>
								</div>
							))}
						</div>
					)}
				</CardContent>
			</Card>

			<AlertDialog
				open={selectedMemory !== null}
				onOpenChange={(open) => {
					if (!open && !deleting) setSelectedMemory(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Remove this memory?</AlertDialogTitle>
						<AlertDialogDescription>
							Bifrost will no longer be able to find or use it.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deleting}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={deleting}
							onClick={handleDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleting ? "Removing…" : "Remove"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
