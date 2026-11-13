import { useCallback, useRef, useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { AlertTriangle, CheckCircle2, Upload, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
	importEntities,
	importAll,
	type EntityType,
	type ImportResult,
} from "@/services/exportImport";
import { useOrganizations } from "@/hooks/useOrganizations";

interface PreviewItem {
	id: string;
	label: string;
	sublabel?: string;
}

interface PreviewSection {
	entityType: string;
	items: PreviewItem[];
	rawData: Record<string, unknown>;
}

interface ImportDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	entityType: EntityType | "all";
	onImportComplete?: () => void;
}

/** Build a scope suffix for preview IDs to disambiguate items across orgs. */
function orgScope(orgId: unknown): string {
	return (orgId as string) || "global";
}

/** Format org scope for display. Prefers org name, falls back to truncated UUID. */
function formatOrgScope(orgId: unknown, orgName: unknown): string | undefined {
	if (orgName) return orgName as string;
	const id = orgId as string | null | undefined;
	if (!id) return undefined;
	return `org: ${id.slice(0, 8)}`;
}

function parseEntityJson(data: Record<string, unknown>): PreviewSection | null {
	const entityType = data.entity_type as string | undefined;
	if (!entityType) return null;
	const items = (data.items ?? []) as Record<string, unknown>[];

	// Check if items span multiple orgs - only show scope qualifier if they do
	const orgIds = new Set(items.map((item) => orgScope(item.organization_id)));
	const multiOrg = orgIds.size > 1;

	const previewItems: PreviewItem[] = [];
	if (entityType === "knowledge") {
		// Group by namespace + org scope
		const groups: Record<
			string,
			{ ns: string; orgId: unknown; orgName: unknown; count: number }
		> = {};
		for (const item of items) {
			const ns = (item.namespace as string) || "default";
			const scope = orgScope(item.organization_id);
			const key = `${ns}\0${scope}`;
			if (!groups[key]) {
				groups[key] = {
					ns,
					orgId: item.organization_id,
					orgName: item.organization_name,
					count: 0,
				};
			}
			groups[key].count++;
		}
		for (const [, group] of Object.entries(groups)) {
			const scope = orgScope(group.orgId);
			const orgLabel = multiOrg
				? formatOrgScope(group.orgId, group.orgName)
				: undefined;
			const parts = [
				`${group.count} document${group.count !== 1 ? "s" : ""}`,
			];
			if (orgLabel) parts.push(orgLabel);
			previewItems.push({
				id: `knowledge/${group.ns}\0${scope}`,
				label: group.ns,
				sublabel: parts.join(" · "),
			});
		}
	} else if (entityType === "tables") {
		for (const item of items) {
			const scope = orgScope(item.organization_id);
			const docCount = (
				(item.documents ?? []) as Record<string, unknown>[]
			).length;
			const parts: string[] = [];
			if (docCount) {
				parts.push(`${docCount} row${docCount !== 1 ? "s" : ""}`);
			}
			if (multiOrg) {
				const orgLabel = formatOrgScope(
					item.organization_id,
					item.organization_name,
				);
				if (orgLabel) parts.push(orgLabel);
			}
			previewItems.push({
				id: `tables/${item.name as string}\0${scope}`,
				label: item.name as string,
				sublabel: parts.join(" · ") || undefined,
			});
		}
	} else if (entityType === "configs") {
		for (const item of items) {
			const scope = orgScope(item.organization_id);
			const parts: string[] = [];
			if (item.config_type) parts.push(item.config_type as string);
			if (multiOrg) {
				const orgLabel = formatOrgScope(
					item.organization_id,
					item.organization_name,
				);
				if (orgLabel) parts.push(orgLabel);
			}
			previewItems.push({
				id: `configs/${item.key as string}\0${scope}`,
				label: item.key as string,
				sublabel: parts.join(" · ") || undefined,
			});
		}
	} else if (entityType === "integrations") {
		for (const item of items) {
			// Integrations don't have a top-level organization_id,
			// but their mappings do. Show one row per integration.
			previewItems.push({
				id: `integrations/${item.name as string}`,
				label: item.name as string,
			});
		}
	}

	return { entityType, items: previewItems, rawData: data };
}

function filterEntityData(
	section: PreviewSection,
	selectedIds: Set<string>,
): Record<string, unknown> {
	const data = { ...section.rawData };
	const items = (data.items ?? []) as Record<string, unknown>[];

	let filtered: Record<string, unknown>[];
	if (section.entityType === "knowledge") {
		// Build set of selected namespace+scope keys
		const selectedKeys = new Set<string>();
		for (const id of selectedIds) {
			if (id.startsWith("knowledge/")) {
				selectedKeys.add(id.slice("knowledge/".length));
			}
		}
		filtered = items.filter((item) => {
			const ns = (item.namespace as string) || "default";
			const scope = orgScope(item.organization_id);
			return selectedKeys.has(`${ns}\0${scope}`);
		});
	} else if (section.entityType === "tables") {
		filtered = items.filter((item) => {
			const scope = orgScope(item.organization_id);
			return selectedIds.has(`tables/${item.name as string}\0${scope}`);
		});
	} else if (section.entityType === "configs") {
		filtered = items.filter((item) => {
			const scope = orgScope(item.organization_id);
			return selectedIds.has(`configs/${item.key as string}\0${scope}`);
		});
	} else if (section.entityType === "integrations") {
		filtered = items.filter((item) =>
			selectedIds.has(`integrations/${item.name as string}`),
		);
	} else {
		filtered = items;
	}

	data.items = filtered;
	data.item_count = filtered.length;
	return data;
}

export function ImportDialog({
	open,
	onOpenChange,
	entityType,
	onImportComplete,
}: ImportDialogProps) {
	const fileInputRef = useRef<HTMLInputElement>(null);
	const [file, setFile] = useState<File | null>(null);
	const [isDragOver, setIsDragOver] = useState(false);
	const [replaceExisting, setReplaceExisting] = useState(true);
	const [sourceSecretKey, setSourceSecretKey] = useState("");
	const [showSecretFields, setShowSecretFields] = useState(false);
	const [isImporting, setIsImporting] = useState(false);
	const [importError, setImportError] = useState<string | null>(null);
	const [result, setResult] = useState<ImportResult | ImportResult[] | null>(
		null,
	);

	// Preview state (JSON only - ZIP files pass through as-is)
	const [previewSection, setPreviewSection] = useState<PreviewSection | null>(
		null,
	);
	const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set());
	const [parseError, setParseError] = useState<string | null>(null);

	// Target org override: undefined=from file, null=Global, string=org UUID
	const [targetOrgId, setTargetOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [hasOrgScopedItems, setHasOrgScopedItems] = useState(false);
	const { data: orgsData } = useOrganizations({
		enabled: hasOrgScopedItems,
	});
	const openFilePicker = () => {
		if (!isImporting) fileInputRef.current?.click();
	};

	const parseJsonFile = async (f: File) => {
		try {
			setParseError(null);
			const text = await f.text();
			const data = JSON.parse(text) as Record<string, unknown>;
			const section = parseEntityJson(data);
			if (!section || section.items.length === 0) {
				setParseError("No importable items found in this file");
				return;
			}
			setPreviewSection(section);
			setSelectedItems(new Set(section.items.map((item) => item.id)));
			// Detect org-scoped items for target org selector
			const rawItems = (data.items ?? []) as Record<string, unknown>[];
			const hasOrg = rawItems.some((item) => item.organization_id);
			setHasOrgScopedItems(hasOrg);
		} catch {
			setParseError("Failed to parse JSON file");
		}
	};

	const detectEncryptedValues = async (f: File) => {
		try {
			const text = await f.text();
			const data = JSON.parse(text);
			if (data.contains_encrypted_values) {
				setShowSecretFields(true);
			}
		} catch {
			if (f.name.endsWith(".zip")) {
				setShowSecretFields(true);
			}
		}
	};

	const handleFilePicked = useCallback(
		(f: File) => {
			if (isImporting) return;
			setFile(f);
			setPreviewSection(null);
			setSelectedItems(new Set());
			setParseError(null);
			setImportError(null);
			setTargetOrgId(undefined);
			setHasOrgScopedItems(false);
			detectEncryptedValues(f);
			// Only parse JSON files for preview; ZIP files pass through
			if (!f.name.endsWith(".zip")) {
				void parseJsonFile(f);
			}
		},
		[isImporting],
	);

	const handleDrop = useCallback(
		(e: React.DragEvent) => {
			e.preventDefault();
			setIsDragOver(false);
			const droppedFile = e.dataTransfer.files?.[0];
			if (droppedFile) {
				handleFilePicked(droppedFile);
			}
		},
		[handleFilePicked],
	);

	const toggleItem = (id: string) => {
		setSelectedItems((prev) => {
			const next = new Set(prev);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
			}
			return next;
		});
	};

	const toggleAll = () => {
		if (!previewSection) return;
		const allIds = previewSection.items.map((i) => i.id);
		const allSelected = allIds.every((id) => selectedItems.has(id));
		if (allSelected) {
			setSelectedItems(new Set());
		} else {
			setSelectedItems(new Set(allIds));
		}
	};

	const handleImport = async () => {
		if (!file || isImporting) return;
		setIsImporting(true);
		setImportError(null);
		try {
			const options = {
				replaceExisting,
				sourceSecretKey: sourceSecretKey || undefined,
				targetOrganizationId: targetOrgId,
			};

			let importResult;
			if (entityType === "all") {
				// ZIP - pass through as-is
				importResult = await importAll(file, options);
			} else if (previewSection) {
				// JSON - filter to selected items
				const filtered = filterEntityData(
					previewSection,
					selectedItems,
				);
				const json = JSON.stringify(filtered, null, 2);
				const filteredFile = new File(
					[json],
					file.name || "import.json",
					{ type: "application/json" },
				);
				importResult = await importEntities(
					entityType,
					filteredFile,
					options,
				);
			} else {
				// Fallback - send original file
				importResult = await importEntities(entityType, file, options);
			}
			setResult(importResult);
			toast.success("Import completed");
			onImportComplete?.();
		} catch (e) {
			setImportError(e instanceof Error ? e.message : "Import failed");
		} finally {
			setIsImporting(false);
		}
	};

	const handleClose = () => {
		if (isImporting) return;
		setFile(null);
		setResult(null);
		setPreviewSection(null);
		setSelectedItems(new Set());
		setParseError(null);
		setShowSecretFields(false);
		setSourceSecretKey("");
		setTargetOrgId(undefined);
		setHasOrgScopedItems(false);
		setImportError(null);
		onOpenChange(false);
	};

	const accept = entityType === "all" ? ".zip" : ".json";
	const label = entityType === "all" ? "All Entities (ZIP)" : entityType;
	const isZip = entityType === "all";
	const selectedCount = selectedItems.size;
	const totalCount = previewSection?.items.length ?? 0;
	const canImport = isZip ? !!file : selectedCount > 0;

	return (
		<Dialog open={open} onOpenChange={handleClose}>
			<DialogContent className="flex w-[calc(100vw-1rem)] max-h-[92dvh] flex-col overflow-hidden [&>*]:min-w-0 sm:max-w-2xl">
				<DialogHeader className="shrink-0">
					<DialogTitle>Import {label}</DialogTitle>
					<DialogDescription>
						Upload a previously exported file to import entities.
					</DialogDescription>
				</DialogHeader>

				<div className="min-h-0 flex-1 overflow-y-auto">
					{!result ? (
						<fieldset
							disabled={isImporting}
							className="min-w-0 space-y-4"
							aria-busy={isImporting}
						>
							<div
								role="button"
								tabIndex={isImporting ? -1 : 0}
								aria-disabled={isImporting}
								aria-label="Choose import file"
								className={`flex min-h-44 flex-col items-center justify-center rounded-[var(--bf-radius-control)] border-2 border-dashed p-8 text-center transition-colors motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
									isDragOver
										? "border-primary bg-primary/5"
										: "border-border hover:border-primary/50"
								}`}
								onDragOver={(e) => {
									e.preventDefault();
									setIsDragOver(true);
								}}
								onDragLeave={() => setIsDragOver(false)}
								onDrop={handleDrop}
								onClick={(e) => {
									if (e.target !== fileInputRef.current)
										openFilePicker();
								}}
								onKeyDown={(e) => {
									if (
										e.target === e.currentTarget &&
										(e.key === "Enter" || e.key === " ")
									) {
										e.preventDefault();
										openFilePicker();
									}
								}}
							>
								<input
									ref={fileInputRef}
									id="import-file-input"
									type="file"
									accept={accept}
									onChange={(e) => {
										const f = e.target.files?.[0];
										if (f) handleFilePicked(f);
									}}
									className="hidden"
								/>
								{file ? (
									<div className="flex flex-col items-center gap-2">
										<CheckCircle2 className="h-8 w-8 text-[var(--bf-success)]" />
										<p className="text-sm font-medium">
											{file.name}
										</p>
										<p className="text-xs text-muted-foreground">
											Click or drag to replace
										</p>
									</div>
								) : (
									<div className="flex flex-col items-center gap-2">
										<Upload className="h-8 w-8 text-muted-foreground" />
										<p className="text-sm font-medium">
											Drop {accept} file here
										</p>
										<p className="text-xs text-muted-foreground">
											or click to browse
										</p>
									</div>
								)}
							</div>

							{parseError && (
								<div
									role="alert"
									className="flex items-start gap-2 rounded-[var(--bf-radius-control)] border border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)]/60 p-3"
								>
									<AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--bf-danger)]" />
									<p className="text-xs text-[var(--bf-danger)]">
										{parseError}
									</p>
								</div>
							)}

							{importError && (
								<div
									role="alert"
									className="flex items-start gap-2 rounded-[var(--bf-radius-control)] border border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)]/60 p-3"
								>
									<AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--bf-danger)]" />
									<p className="text-xs text-[var(--bf-danger)]">
										{importError}
									</p>
								</div>
							)}

							{previewSection && (
								<div className="space-y-3">
									<div className="flex items-center justify-between">
										<button
											type="button"
											onClick={toggleAll}
											className="min-h-11 rounded-[var(--bf-radius-control)] px-2 text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
										>
											Contents
										</button>
										<p className="text-xs text-muted-foreground">
											{selectedCount} of {totalCount}{" "}
											selected
										</p>
									</div>
									<div className="max-h-60 overflow-y-auto rounded-[var(--bf-radius-control)] border border-border/70 divide-y divide-border/70 bg-background">
										{previewSection.items.map((item) => {
											const previewId = `preview-${encodeURIComponent(item.id)}`;
											return (
												<label
													key={item.id}
													htmlFor={previewId}
													className="flex min-h-11 cursor-pointer items-center gap-3 px-3 py-2 hover:bg-muted/50"
												>
													<Checkbox
														id={previewId}
														checked={selectedItems.has(
															item.id,
														)}
														onCheckedChange={() =>
															toggleItem(item.id)
														}
													/>
													<span className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">
														{item.label}
													</span>
													{item.sublabel && (
														<span className="ml-auto flex-shrink-0 text-xs text-muted-foreground">
															{item.sublabel}
														</span>
													)}
												</label>
											);
										})}
									</div>
								</div>
							)}

							{hasOrgScopedItems && (
								<div className="space-y-2">
									<Label htmlFor="target-org">
										Target Organization
									</Label>
									<Select
										value={
											targetOrgId === undefined
												? "__from_file__"
												: targetOrgId === null
													? "__global__"
													: targetOrgId
										}
										onValueChange={(v) => {
											if (v === "__from_file__")
												setTargetOrgId(undefined);
											else if (v === "__global__")
												setTargetOrgId(null);
											else setTargetOrgId(v);
										}}
									>
										<SelectTrigger
											id="target-org"
											className="w-full min-w-0"
										>
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="__from_file__">
												From file (resolve by name/ID)
											</SelectItem>
											<SelectItem value="__global__">
												Global (no organization)
											</SelectItem>
											{orgsData?.map((org) => (
												<SelectItem
													key={org.id}
													value={org.id}
												>
													{org.name}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									<p className="text-xs text-muted-foreground">
										Override which organization imported
										items belong to.
									</p>
								</div>
							)}

							{showSecretFields && (
								<div className="space-y-3">
									<div className="flex items-start gap-2 rounded-[var(--bf-radius-control)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)]/60 p-3">
										<AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--bf-warning)]" />
										<p className="text-xs text-[var(--bf-warning)]">
											This file contains encrypted values.
											Provide the source instance's secret
											key to re-encrypt them for this
											instance.
										</p>
									</div>
									<div className="space-y-2">
										<Label htmlFor="source-key">
											Source Secret Key
										</Label>
										<Input
											id="source-key"
											type="password"
											value={sourceSecretKey}
											onChange={(e) =>
												setSourceSecretKey(
													e.target.value,
												)
											}
											placeholder="BIFROST_SECRET_KEY from source instance"
										/>
									</div>
								</div>
							)}

							<div className="flex min-h-11 items-center gap-2">
								<Checkbox
									id="replace-existing"
									checked={replaceExisting}
									onCheckedChange={(checked) =>
										setReplaceExisting(checked === true)
									}
								/>
								<Label
									htmlFor="replace-existing"
									className="text-sm"
								>
									Replace existing matches
								</Label>
							</div>
						</fieldset>
					) : (
						/* Results display */
						<div className="space-y-3">
							{(Array.isArray(result) ? result : [result]).map(
								(r, i) => (
									<div
										key={i}
										className="space-y-2 rounded-[var(--bf-radius-control)] border border-border/70 bg-background p-3"
									>
										<p className="text-sm font-medium capitalize">
											{r.entity_type}
										</p>
										<div className="flex flex-wrap gap-2">
											{r.created > 0 && (
												<Badge variant="default">
													{r.created} created
												</Badge>
											)}
											{r.updated > 0 && (
												<Badge variant="secondary">
													{r.updated} updated
												</Badge>
											)}
											{r.skipped > 0 && (
												<Badge variant="outline">
													{r.skipped} skipped
												</Badge>
											)}
											{r.errors > 0 && (
												<Badge variant="destructive">
													{r.errors} errors
												</Badge>
											)}
										</div>
										{r.warnings.length > 0 && (
											<div className="space-y-1 text-xs text-[var(--bf-warning)]">
												{r.warnings.map((w, j) => (
													<p key={j}>{w}</p>
												))}
											</div>
										)}
									</div>
								),
							)}
						</div>
					)}
				</div>
				<DialogFooter className="shrink-0 border-t pt-4">
					{!result ? (
						<Button
							className="min-h-11"
							onClick={handleImport}
							disabled={!file || isImporting || !canImport}
						>
							{isImporting && (
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							)}
							{isZip
								? "Import"
								: `Import${selectedCount > 0 ? ` (${selectedCount})` : ""}`}
						</Button>
					) : (
						<Button className="min-h-11" onClick={handleClose}>
							Done
						</Button>
					)}
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
