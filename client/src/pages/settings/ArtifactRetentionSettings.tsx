import { useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
	cleanupExpiredArtifacts,
	getArtifactRetentionSettings,
	updateArtifactRetentionSettings,
} from "@/services/artifactRetention";

export function ArtifactRetentionSettings() {
	const [enabled, setEnabled] = useState(false);
	const [retentionDays, setRetentionDays] = useState(90);
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [cleaning, setCleaning] = useState(false);

	useEffect(() => {
		let active = true;
		getArtifactRetentionSettings()
			.then((settings) => {
				if (!active) return;
				setEnabled(settings.enabled);
				setRetentionDays(settings.retention_days);
			})
			.catch(() =>
				toast.error("Failed to load artifact retention settings"),
			)
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, []);

	const saveSettings = async (
		nextEnabled = enabled,
		nextDays = retentionDays,
	) => {
		setSaving(true);
		const normalizedDays = Math.max(
			1,
			Math.min(3650, Math.round(nextDays)),
		);
		try {
			const settings = await updateArtifactRetentionSettings({
				enabled: nextEnabled,
				retention_days: normalizedDays,
			});
			setEnabled(settings.enabled);
			setRetentionDays(settings.retention_days);
			toast.success("Artifact retention saved");
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update artifact retention settings",
			);
		} finally {
			setSaving(false);
		}
	};

	const handleCleanup = async () => {
		setCleaning(true);
		try {
			const result = await cleanupExpiredArtifacts();
			toast.success(
				result.reused
					? "Artifact cleanup already queued"
					: "Artifact cleanup queued",
				{
					description: "Progress is available in notifications.",
				},
			);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to clean up expired artifacts",
			);
		} finally {
			setCleaning(false);
		}
	};

	return (
		<Card>
			<CardHeader>
				<CardTitle>Artifact Retention</CardTitle>
				<CardDescription>
					Set how long Chat attachments and generated artifacts are
					retained.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				<div className="flex items-center justify-between gap-6">
					<div className="space-y-1">
						<Label htmlFor="artifact-retention-enabled">
							Enable Scheduled Cleanup
						</Label>
						<p className="text-sm text-muted-foreground">
							Expired Chat files are removed during the daily
							maintenance window.
						</p>
					</div>
					<div className="flex items-center gap-2">
						{(loading || saving) && (
							<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
						)}
						<Switch
							id="artifact-retention-enabled"
							checked={enabled}
							disabled={loading || saving}
							onCheckedChange={(checked) =>
								void saveSettings(checked)
							}
						/>
					</div>
				</div>

				<div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
					<div className="space-y-2">
						<Label htmlFor="artifact-retention-days">
							Retention Days
						</Label>
						<Input
							id="artifact-retention-days"
							type="number"
							min={1}
							max={3650}
							value={retentionDays}
							disabled={loading || saving}
							onChange={(event) =>
								setRetentionDays(
									Number(event.target.value) || 1,
								)
							}
							onBlur={() => void saveSettings()}
							className="w-32"
						/>
					</div>
					<Button
						type="button"
						variant="outline"
						onClick={handleCleanup}
						disabled={loading || saving || cleaning}
					>
						{cleaning ? (
							<Loader2 className="h-4 w-4 mr-2 animate-spin" />
						) : (
							<Trash2 className="h-4 w-4 mr-2" />
						)}
						Run Cleanup
					</Button>
				</div>
			</CardContent>
		</Card>
	);
}
