import { useEffect, useState } from "react";
import { ArrowUpCircle } from "lucide-react";
import { toast } from "sonner";

import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { SettingsToggleRow } from "@/components/shared/SettingsToggleRow";
import {
	getAppSdkUpgradeSettings,
	updateAppSdkUpgradeSettings,
} from "@/services/appSdkUpgradeSettings";

const TOGGLE_ID = "app-sdk-upgrades-enabled";

export function AppSdkUpgradeSettings() {
	const [enabled, setEnabled] = useState(false);
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState(false);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saving, setSaving] = useState(false);
	const [failedEnabled, setFailedEnabled] = useState<boolean | null>(null);

	useEffect(() => {
		let active = true;
		getAppSdkUpgradeSettings()
			.then((settings) => {
				if (active) setEnabled(settings.enabled);
			})
			.catch(() => {
				if (active) setLoadError(true);
			})
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, [loadAttempt]);

	const save = async (nextEnabled: boolean) => {
		if (loading || saving || loadError || failedEnabled !== null) return;
		setSaving(true);
		try {
			const settings = await updateAppSdkUpgradeSettings(nextEnabled);
			setEnabled(settings.enabled);
			toast.success(
				settings.enabled
					? "Automatic App SDK upgrades enabled"
					: "Automatic App SDK upgrades disabled",
			);
		} catch {
			setFailedEnabled(nextEnabled);
		} finally {
			setSaving(false);
		}
	};

	const retrySave = async () => {
		if (failedEnabled === null || saving) return;
		const nextEnabled = failedEnabled;
		setFailedEnabled(null);
		setSaving(true);
		try {
			const settings = await updateAppSdkUpgradeSettings(nextEnabled);
			setEnabled(settings.enabled);
			toast.success(
				settings.enabled
					? "Automatic App SDK upgrades enabled"
					: "Automatic App SDK upgrades disabled",
			);
		} catch {
			setFailedEnabled(nextEnabled);
		} finally {
			setSaving(false);
		}
	};

	return (
		<Card>
			<CardHeader>
				<div className="flex items-center gap-2">
					<ArrowUpCircle className="h-5 w-5" />
					<CardTitle>App SDK Upgrades</CardTitle>
				</div>
				<CardDescription>
					After an instance upgrade, update app SDKs when no app code changes
					are needed. Progress and failures appear in Notifications.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{loadError && (
					<SettingsLoadError
						name="App SDK upgrade settings"
						onRetry={() => {
							setLoading(true);
							setLoadError(false);
							setLoadAttempt((value) => value + 1);
						}}
					/>
				)}
				{failedEnabled !== null && (
					<div
						role="alert"
						className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 p-[var(--bf-surface-pad)]"
					>
						<p className="text-sm">
							Could not confirm that App SDK upgrade settings were saved. Retry
							to apply your choice.
						</p>
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							disabled={saving}
							onClick={() => void retrySave()}
						>
							Retry Save
						</Button>
					</div>
				)}
				<SettingsToggleRow
					id={TOGGLE_ID}
					label="Enable Automatic App SDK Upgrades"
					description="Failed updates keep the current deployment active and require a manual retry."
					checked={enabled}
					disabled={loading || saving || loadError || failedEnabled !== null}
					busy={loading ? "loading" : saving ? "saving" : undefined}
					onChange={(nextEnabled) => void save(nextEnabled)}
				/>
			</CardContent>
		</Card>
	);
}
