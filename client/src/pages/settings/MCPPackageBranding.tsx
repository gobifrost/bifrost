import { useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { LogoDropZone } from "@/components/LogoDropZone";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { $api } from "@/lib/api-client";
import { downloadBifrostRunPlugin } from "@/services/bifrostRun";

function isValidName(name: string) {
	return (
		name.length >= 1 &&
		name.length <= 60 &&
		/^[A-Za-z0-9][A-Za-z0-9 _-]*$/.test(name)
	);
}

function deriveSlug(name: string) {
	return name
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
}

export function MCPPackageBranding() {
	const downloadPending = useRef(false);
	const [draft, setDraft] = useState<string | null>(null);
	const [loadedName, setLoadedName] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [downloading, setDownloading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const { data, isLoading, isError, refetch } = $api.useQuery(
		"get",
		"/api/mcp/run/branding",
		undefined,
		{ staleTime: 5 * 60 * 1000 },
	);
	const saveMutation = $api.useMutation("put", "/api/mcp/run/branding");

	if (data && loadedName !== data.name) {
		setLoadedName(data.name);
		if (draft === null) setDraft(data.name);
	}

	const name = draft ?? data?.name ?? "Bifrost Agent";
	const identifier = deriveSlug(name);
	const hasChanges = !!data && name !== data.name;

	const save = async () => {
		const normalized = name.trim();
		if (!isValidName(normalized)) {
			setError(
				"Start with a letter or number. Use up to 60 letters, numbers, spaces, hyphens, or underscores.",
			);
			return;
		}
		setSaving(true);
		setError(null);
		try {
			const updated = await saveMutation.mutateAsync({
				body: { name: normalized },
			});
			setDraft(updated.name);
			setLoadedName(updated.name);
			toast.success("Package name saved");
			await refetch();
		} catch {
			setError(
				"Could not save the package name. Your changes are still here. Try again.",
			);
		} finally {
			setSaving(false);
		}
	};

	const download = async () => {
		if (downloadPending.current) return;
		downloadPending.current = true;
		setDownloading(true);
		setError(null);
		try {
			const { blob, filename } = await downloadBifrostRunPlugin();
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = filename;
			link.click();
			URL.revokeObjectURL(url);
			toast.success("Agent plugin downloaded");
		} catch {
			setError("Could not download the agent plugin. Try again.");
		} finally {
			downloadPending.current = false;
			setDownloading(false);
		}
	};

	return (
		<Card>
			<CardHeader>
				<CardTitle role="heading" aria-level={2}>Agent Plugin Package</CardTitle>
				<CardDescription>
					Customize the name and logo included when people install
					your Bifrost Agent plugin.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-6">
				{isLoading ? (
					<div
						role="status"
						className="flex items-center gap-2 text-sm text-muted-foreground"
					>
						<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
						Loading package branding
					</div>
				) : null}
				{isError ? (
					<p role="alert" className="text-sm text-destructive">
						Could not load package branding.{" "}
						<button
							type="button"
							className="underline"
							onClick={() => void refetch()}
						>
							Try again
						</button>
					</p>
				) : null}
				{data ? (
					<>
						<div className="flex flex-col gap-5 sm:flex-row sm:items-start">
							<LogoDropZone
								uploadUrl="/api/mcp/run/branding/logo"
								deleteUrl="/api/mcp/run/branding/logo"
								previewUrl={
									data.logo_url ??
									"/api/mcp/run/branding/logo"
								}
								fallback={
									<img
										src="/logo.svg"
										alt="Default Bifrost logo"
										className="size-16 object-contain"
									/>
								}
								size={128}
								ariaLabel="Upload package logo"
								onChange={() => void refetch()}
							/>
							<div className="space-y-2 text-sm text-muted-foreground">
								<p className="font-medium text-foreground">
									Package Logo
								</p>
								<p>
									Upload a PNG, JPEG, or SVG up to 5 MB. Sized
									to a square PNG for the plugin without
									cropping.
								</p>
							</div>
						</div>
						<div className="space-y-2">
							<Label htmlFor="mcp-package-name">
								Package Name
							</Label>
							<Input
								id="mcp-package-name"
								value={name}
								maxLength={60}
								onChange={(event) => {
									setDraft(event.target.value);
									setError(null);
								}}
								aria-describedby="mcp-package-identifier"
							/>
							<p
								id="mcp-package-identifier"
								className="text-sm text-muted-foreground"
							>
								Package identifier: <code>{identifier}</code>
							</p>
						</div>
						<p className="text-sm text-muted-foreground">
							Changing the package name requires reinstalling
							existing plugins.
						</p>
						{hasChanges ? (
							<p
								role="status"
								className="text-sm text-muted-foreground"
							>
								Save the package name before downloading the
								plugin.
							</p>
						) : null}
						{error ? (
							<p
								role="alert"
								className="text-sm text-destructive"
							>
								{error}
							</p>
						) : null}
						<div className="flex flex-col gap-3 sm:flex-row">
							<Button
								type="button"
								className="min-h-11"
								disabled={saving || !hasChanges}
								onClick={() => void save()}
							>
								{saving ? (
									<>
										<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
										Saving…
									</>
								) : (
									"Save Package"
								)}
							</Button>
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={downloading || saving || hasChanges}
								onClick={() => void download()}
							>
								{downloading ? (
									<>
										<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
										Downloading…
									</>
								) : (
									<>
										<Download className="size-4" />
										Download Agent Plugin
									</>
								)}
							</Button>
						</div>
					</>
				) : null}
			</CardContent>
		</Card>
	);
}
