/**
 * Form HMAC Integration Section
 *
 * HMAC sharing tab for trusted systems that sign dynamic embed parameters.
 */

import { useState, useEffect, useCallback } from "react";
import {
	Plus,
	Trash2,
	Copy,
	Check,
	AlertTriangle,
	Link,
	X,
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
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
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";

// ============================================================================
// Types
// ============================================================================

type HmacScheme = "shopify" | "halopsa";

interface EmbedSecret {
	id: string;
	name: string;
	is_active: boolean;
	hmac_scheme: HmacScheme;
	created_at: string;
}

interface EmbedSecretCreated extends EmbedSecret {
	raw_secret: string;
}

interface FormEmbedSectionProps {
	formId: string;
}

// ============================================================================
// Component
// ============================================================================

export function FormEmbedSection({ formId }: FormEmbedSectionProps) {
	const [secrets, setSecrets] = useState<EmbedSecret[]>([]);
	const [isLoading, setIsLoading] = useState(false);

	// Create form state
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [createName, setCreateName] = useState("");
	const [createSecret, setCreateSecret] = useState("");
	const [createScheme, setCreateScheme] = useState<HmacScheme>("shopify");
	const [isCreating, setIsCreating] = useState(false);

	// Reveal state (shown once after creation)
	const [revealedSecret, setRevealedSecret] =
		useState<EmbedSecretCreated | null>(null);
	const [copied, setCopied] = useState(false);

	// Delete confirmation state
	const [deleteTarget, setDeleteTarget] = useState<EmbedSecret | null>(null);

	// ========================================================================
	// Data fetching
	// ========================================================================

	const fetchSecrets = useCallback(async () => {
		setIsLoading(true);
		try {
			const res = await authFetch(`/api/forms/${formId}/embed-secrets`);
			if (res.ok) {
				setSecrets(await res.json());
			}
		} catch {
			toast.error("Failed to load embed secrets");
		} finally {
			setIsLoading(false);
		}
	}, [formId]);

	useEffect(() => {
		void (async () => {
			await fetchSecrets();
		})();
	}, [fetchSecrets]);

	// ========================================================================
	// Actions
	// ========================================================================

	const handleCreate = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!createName.trim()) return;

		setIsCreating(true);
		try {
			const res = await authFetch(`/api/forms/${formId}/embed-secrets`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: createName.trim(),
					hmac_scheme: createScheme,
					...(createSecret.trim() && { secret: createSecret.trim() }),
				}),
			});
			if (!res.ok) throw new Error(await res.text());
			const created: EmbedSecretCreated = await res.json();
			setRevealedSecret(created);
			setIsCreateOpen(false);
			setCreateName("");
			setCreateSecret("");
			setCreateScheme("shopify");
			fetchSecrets();
			toast.success("Embed secret created");
		} catch {
			toast.error("Failed to create embed secret");
		} finally {
			setIsCreating(false);
		}
	};

	const handleToggleActive = async (secret: EmbedSecret) => {
		try {
			const res = await authFetch(
				`/api/forms/${formId}/embed-secrets/${secret.id}`,
				{
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ is_active: !secret.is_active }),
				},
			);
			if (!res.ok) throw new Error(await res.text());
			fetchSecrets();
			toast.success(
				secret.is_active ? "Secret deactivated" : "Secret activated",
			);
		} catch {
			toast.error("Failed to update secret");
		}
	};

	const handleDelete = async () => {
		if (!deleteTarget) return;
		try {
			const res = await authFetch(
				`/api/forms/${formId}/embed-secrets/${deleteTarget.id}`,
				{ method: "DELETE" },
			);
			if (!res.ok) throw new Error(await res.text());
			setDeleteTarget(null);
			fetchSecrets();
			toast.success("Secret deleted");
		} catch {
			toast.error("Failed to delete secret");
		}
	};

	const handleCopy = (text: string) => {
		navigator.clipboard.writeText(text);
		setCopied(true);
		setTimeout(() => setCopied(false), 2000);
	};

	// ========================================================================
	// Code snippets
	// ========================================================================

	const embedUrl = `${window.location.origin}/embed/forms/${formId}`;

	const iframeSnippet = `<iframe
  src="${embedUrl}?param1=value1&hmac=COMPUTED_HMAC"
  style="width: 100%; height: 600px; border: none;"
  allow="clipboard-write"
></iframe>`;
	// ========================================================================
	// Render
	// ========================================================================

	return (
		<>
			<div className="space-y-4">
				{/* ============ Secrets ============ */}
				<div className="space-y-3">
					<div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
						<p className="text-sm text-muted-foreground">
							Shared secrets for HMAC-authenticated iframe
							embedding. After submission, the iframe opens the
							execution result for that signed session.
						</p>
						<Button size="sm" onClick={() => setIsCreateOpen(true)}>
							<Plus className="mr-2 h-4 w-4" />
							Create Secret
						</Button>
					</div>

					{isLoading ? (
						<p className="text-sm text-muted-foreground py-4 text-center">
							Loading...
						</p>
					) : secrets.length === 0 ? (
						<div className="text-center py-4 text-muted-foreground">
							<Link className="h-6 w-6 mx-auto mb-2 opacity-50" />
							<p className="text-sm">
								No embed secrets configured.
							</p>
							<p className="text-xs mt-1">
								Create a secret to enable iframe embedding.
							</p>
						</div>
					) : (
						<div className="space-y-2">
							{secrets.map((secret) => (
								<div
									key={secret.id}
									className={`flex flex-col items-start justify-between gap-3 rounded-lg p-3 ring-1 ring-foreground/5 sm:flex-row sm:items-center ${
										secret.is_active
											? "border-l-4 border-l-green-500"
											: "border-l-4 border-l-gray-300 opacity-60"
									}`}
								>
									<div className="flex flex-wrap items-center gap-3">
										<div>
											<p className="text-sm font-medium">
												{secret.name}
											</p>
											<p className="text-xs text-muted-foreground">
												Created{" "}
												{new Date(
													secret.created_at,
												).toLocaleDateString()}
											</p>
										</div>
										<Badge
											variant={
												secret.is_active
													? "default"
													: "secondary"
											}
										>
											{secret.is_active
												? "Active"
												: "Inactive"}
										</Badge>
										<Badge variant="outline">
											{secret.hmac_scheme === "halopsa"
												? "HaloPSA"
												: "Standard"}
										</Badge>
									</div>
									<div className="flex items-center gap-1 self-end sm:self-auto">
										<Button
											variant="ghost"
											size="sm"
											onClick={() =>
												handleToggleActive(secret)
											}
										>
											{secret.is_active
												? "Deactivate"
												: "Activate"}
										</Button>
										<Button
											variant="ghost"
											size="icon"
											onClick={() =>
												setDeleteTarget(secret)
											}
										>
											<Trash2 className="h-4 w-4 text-destructive" />
										</Button>
									</div>
								</div>
							))}
						</div>
					)}

					{/* Inline create form */}
					{isCreateOpen && (
						<form
							onSubmit={handleCreate}
							className="space-y-3 rounded-lg bg-muted/50 p-4 ring-1 ring-foreground/5"
						>
							<div className="space-y-2">
								<Label htmlFor="embed-secret-name">Name</Label>
								<Input
									id="embed-secret-name"
									placeholder="e.g., Halo Production"
									value={createName}
									onChange={(e) =>
										setCreateName(e.target.value)
									}
									autoFocus
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="embed-secret-value">
									Secret (optional)
								</Label>
								<Input
									id="embed-secret-value"
									placeholder="Leave blank to auto-generate"
									value={createSecret}
									onChange={(e) =>
										setCreateSecret(e.target.value)
									}
									className="font-mono text-sm"
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="embed-secret-scheme">
									HMAC scheme
								</Label>
								<Select
									value={createScheme}
									onValueChange={(v) =>
										setCreateScheme(v as HmacScheme)
									}
								>
									<SelectTrigger id="embed-secret-scheme">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="shopify">
											Standard
										</SelectItem>
										<SelectItem value="halopsa">
											HaloPSA
										</SelectItem>
									</SelectContent>
								</Select>
								<p className="text-xs text-muted-foreground">
									{createScheme === "shopify"
										? "Signs all query parameters (recommended for most integrations)."
										: "Signs only agent_id. Use for HaloPSA Custom Tab embeds."}
								</p>
							</div>
							<div className="flex gap-2 justify-end">
								<Button
									type="button"
									variant="outline"
									size="sm"
									onClick={() => {
										setIsCreateOpen(false);
										setCreateName("");
										setCreateSecret("");
										setCreateScheme("shopify");
									}}
								>
									Cancel
								</Button>
								<Button
									type="submit"
									size="sm"
									disabled={isCreating || !createName.trim()}
								>
									{isCreating ? "Creating..." : "Add"}
								</Button>
							</div>
						</form>
					)}

					{/* One-time secret reveal */}
					{revealedSecret && (
						<div className="relative rounded-lg bg-amber-500/10 p-4 pr-10 space-y-3 ring-1 ring-amber-500/30">
							<button
								type="button"
								className="absolute top-3 right-3 text-muted-foreground hover:text-foreground"
								onClick={() => setRevealedSecret(null)}
							>
								<X className="h-4 w-4" />
							</button>
							<div className="flex items-start gap-2">
								<AlertTriangle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
								<p className="text-sm font-medium">
									Copy this secret now — it will not be shown
									again.
								</p>
							</div>
							<div className="flex gap-2">
								<code className="flex-1 rounded-md bg-black/30 px-3 py-2 font-mono text-sm select-all truncate">
									{revealedSecret.raw_secret}
								</code>
								<Button
									variant="outline"
									size="icon"
									className="shrink-0"
									onClick={() =>
										handleCopy(revealedSecret.raw_secret)
									}
								>
									{copied ? (
										<Check className="h-4 w-4" />
									) : (
										<Copy className="h-4 w-4" />
									)}
								</Button>
							</div>
						</div>
					)}
				</div>

				{/* ============ Integration Guide ============ */}
				<div className="space-y-2 pt-2">
					<p className="text-xs text-muted-foreground">
						Embed iframe
					</p>
					<div className="relative rounded-md overflow-hidden">
						<SyntaxHighlighter
							language="html"
							style={oneDark}
							wrapLongLines
							codeTagProps={{
								style: {
									whiteSpace: "pre-wrap",
									wordBreak: "break-all",
								},
							}}
							customStyle={{
								margin: 0,
								paddingRight: "2.5rem",
								fontSize: "0.75rem",
							}}
						>
							{iframeSnippet}
						</SyntaxHighlighter>
						<Button
							variant="ghost"
							size="icon"
							className="absolute top-2 right-2 h-6 w-6"
							onClick={() => handleCopy(iframeSnippet)}
						>
							<Copy className="h-3 w-3" />
						</Button>
					</div>
				</div>
			</div>

			{/* ============ Delete Confirmation ============ */}
			<AlertDialog
				open={!!deleteTarget}
				onOpenChange={(open) => {
					if (!open) setDeleteTarget(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete embed secret?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently delete &quot;
							{deleteTarget?.name}&quot;. Any integrations using
							this secret will stop working.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={handleDelete}>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
