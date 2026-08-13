import { useEffect, useState } from "react";
import { Brain, Loader2 } from "lucide-react";
import { toast } from "sonner";

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
	getPlatformMemorySettings,
	updatePlatformMemorySettings,
} from "@/services/memory";

export function MemorySettings() {
	const [enabled, setEnabled] = useState(false);
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		let active = true;
		getPlatformMemorySettings()
			.then((settings) => {
				if (active) setEnabled(settings.enabled);
			})
			.catch(() => toast.error("Failed to load memory settings"))
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, []);

	const handleChange = async (nextEnabled: boolean) => {
		setSaving(true);
		try {
			const settings = await updatePlatformMemorySettings(nextEnabled);
			setEnabled(settings.enabled);
			toast.success(
				settings.enabled ? "Memory enabled" : "Memory disabled",
			);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update memory settings",
			);
		} finally {
			setSaving(false);
		}
	};

	return (
		<Card>
			<CardHeader>
				<div className="flex items-center gap-2">
					<Brain className="h-5 w-5" />
					<CardTitle>Memory</CardTitle>
				</div>
				<CardDescription>
					Enable private memory for Bifrost-connected AI assistants.
					Memory uses the embedding configuration above.
				</CardDescription>
			</CardHeader>
			<CardContent>
				<div className="flex items-center justify-between gap-6">
					<div className="space-y-1">
						<Label htmlFor="platform-memory-enabled">
							Enable Memory
						</Label>
						<p className="text-sm text-muted-foreground">
							Users can disable memory in their preferences.
						</p>
					</div>
					<div className="flex items-center gap-2">
						{(loading || saving) && (
							<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
						)}
						<Switch
							id="platform-memory-enabled"
							checked={enabled}
							disabled={loading || saving}
							onCheckedChange={handleChange}
						/>
					</div>
				</div>
			</CardContent>
		</Card>
	);
}
