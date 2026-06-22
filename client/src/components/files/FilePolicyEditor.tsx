import { useMemo, useState } from "react";
import { Save, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import type { FilePolicy } from "@/services/filePolicies";

interface FilePolicyEditorProps {
	path: string;
	value: FilePolicy;
	onSave: (policy: FilePolicy) => void | Promise<void>;
	onDelete: (policy: FilePolicy) => void | Promise<void>;
}

function parsePolicyJson(value: string): FilePolicy | null {
	const parsed = JSON.parse(value) as FilePolicy;
	if (
		!parsed ||
		typeof parsed !== "object" ||
		!parsed.policies ||
		!Array.isArray(parsed.policies.policies)
	) {
		throw new Error("Policy JSON must include policies.policies.");
	}
	return parsed;
}

export function FilePolicyEditor({
	path,
	value,
	onSave,
	onDelete,
}: FilePolicyEditorProps) {
	const initialJson = useMemo(() => JSON.stringify(value, null, 2), [value]);
	const [buffer, setBuffer] = useState(initialJson);
	const [parsed, setParsed] = useState<FilePolicy | null>(value);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);

	function handleChange(next: string) {
		setBuffer(next);
		try {
			const parsedPolicy = parsePolicyJson(next);
			setParsed(parsedPolicy);
			setError(null);
		} catch (err) {
			setParsed(null);
			setError(err instanceof Error ? err.message : "Invalid JSON");
		}
	}

	async function handleSave() {
		if (!parsed) return;
		setSaving(true);
		try {
			await onSave(parsed);
		} finally {
			setSaving(false);
		}
	}

	return (
		<section className="flex min-h-0 flex-col gap-3 border-l bg-background px-4 py-3">
			<div className="flex items-center justify-between gap-3">
				<div className="min-w-0">
					<h2 className="truncate text-sm font-semibold">Policy editor</h2>
					<p className="truncate text-xs text-muted-foreground">{path || value.path || "/"}</p>
				</div>
				<Badge variant="outline">{value.location}</Badge>
			</div>

			<Textarea
				aria-label="Policy JSON"
				value={buffer}
				onChange={(event) => handleChange(event.target.value)}
				className="min-h-[320px] flex-1 resize-none font-mono text-xs"
				spellCheck={false}
			/>

			{error && (
				<p className="text-xs text-destructive" role="alert">
					Invalid JSON: {error}
				</p>
			)}

			<div className="flex items-center justify-end gap-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={() => onDelete(value)}
					disabled={!value.id}
				>
					<Trash2 className="h-4 w-4" />
					Delete
				</Button>
				<Button
					type="button"
					size="sm"
					onClick={handleSave}
					disabled={!parsed || saving}
				>
					<Save className="h-4 w-4" />
					Save policy
				</Button>
			</div>
		</section>
	);
}
