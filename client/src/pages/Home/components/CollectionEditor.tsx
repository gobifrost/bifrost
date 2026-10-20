import { useState } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
	DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
	OrganizationSelect,
	PERSONAL_SCOPE,
} from "@/components/forms/OrganizationSelect";
import type {
	HomeCollection,
	HomeCollectionWrite,
	HomeResource,
} from "@/services/home";
import { ResourceIcon } from "@/components/ResourceIcon";
import { CollectionIconPicker } from "./CollectionIconPicker";
import { useAuthorization } from "@/services/authorization";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";

export function CollectionEditor({
	collection,
	resources,
	onClose,
	onSave,
	onDelete,
	busy,
	error,
	initialConfirmDelete = false,
}: {
	collection?: HomeCollection;
	resources: HomeResource[];
	onClose: () => void;
	onSave: (data: HomeCollectionWrite) => void;
	onDelete: () => void;
	busy: boolean;
	error: string;
	initialConfirmDelete?: boolean;
}) {
	const { authorization, canAnywhere, canAt } = useAuthorization();
	const [name, setName] = useState(collection?.name ?? "");
	const [description, setDescription] = useState(
		collection?.description ?? "",
	);
	const [icon, setIcon] = useState(collection?.icon ?? "folder");
	const [audience, setAudience] = useState(
		collection?.shared
			? (collection.organization_id ?? "global")
			: "personal",
	);
	const [selected, setSelected] = useState(collection?.resource_keys ?? []);
	const [search, setSearch] = useState("");
	const [confirmDelete, setConfirmDelete] = useState(initialConfirmDelete);
	const canSave =
		audience === "personal" ||
		canAt(
			"home.readwrite",
			orgTarget(audience === "global" ? null : audience),
		);
	const knownOrganizations = [
		...new Map(
			resources
				.flatMap((resource) =>
					resource.organization_id
						? [
								{
									id: resource.organization_id,
									name:
										resource.organization_name ??
										"Organization",
								},
							]
						: [],
				)
				.map((organization) => [organization.id, organization]),
		).values(),
	];
	if (
		authorization?.home_organization_id &&
		!knownOrganizations.some(
			(organization) =>
				organization.id === authorization.home_organization_id,
		)
	) {
		knownOrganizations.push({
			id: authorization.home_organization_id,
			name: "Home organization",
		});
	}
	const available = resources.filter(
		(resource) =>
			audience === "personal" ||
			audience === "global" ||
			!resource.organization_id ||
			resource.organization_id === audience,
	);
	const matches = available.filter((resource) =>
		`${resource.name} ${resource.organization_name}`
			.toLowerCase()
			.includes(search.toLowerCase()),
	);
	const resourceMap = new Map(
		resources.map((resource) => [resource.key, resource]),
	);
	const move = (index: number, delta: number) =>
		setSelected((keys) => {
			const next = [...keys];
			[next[index], next[index + delta]] = [
				next[index + delta],
				next[index],
			];
			return next;
		});
	return (
		<Dialog
			open
			onOpenChange={(open) => {
				if (!open && !busy) onClose();
			}}
		>
			<DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
				<DialogHeader className="shrink-0 px-5 py-5 sm:px-6">
					<DialogTitle>
						{collection ? "Edit collection" : "New collection"}
					</DialogTitle>
					<DialogDescription>
						Group apps, forms, and agents. Each item keeps its
						existing access permissions.
					</DialogDescription>
				</DialogHeader>
				<form
					className="flex min-h-0 flex-col overflow-hidden"
					onSubmit={(event) => {
						event.preventDefault();
						if (!canSave) return;
						onSave({
							name: name.trim(),
							description,
							icon,
							shared: audience !== "personal",
							organization_id:
								audience === "personal" || audience === "global"
									? null
									: audience,
							resource_keys: selected,
						});
					}}
				>
					<div className="min-h-0 space-y-5 overflow-y-auto overscroll-contain px-5 pb-5 pt-1 sm:px-6">
						<div className="space-y-2">
							<Label htmlFor="collection-name">Name</Label>
							<Input
								id="collection-name"
								required
								maxLength={100}
								value={name}
								onChange={(event) =>
									setName(event.target.value)
								}
								placeholder="e.g. Employee lifecycle"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="collection-description">
								Description
							</Label>
							<Textarea
								id="collection-description"
								maxLength={500}
								value={description}
								onChange={(event) =>
									setDescription(event.target.value)
								}
								placeholder="What belongs in this collection?"
							/>
						</div>
						{canAnywhere("home.readwrite") && (
							<div className="space-y-2">
								<Label htmlFor="collection-audience">
									Who is this for?
								</Label>
								<OrganizationSelect
									id="collection-audience"
									label="Who is this for?"
									showPersonal
									showGlobal={canAt(
										"home.readwrite",
										GLOBAL_TARGET,
									)}
									filterOrganizations={(organization) =>
										canAt(
											"home.readwrite",
											orgTarget(organization.id),
										)
									}
									knownOrganizations={knownOrganizations}
									loadOrganizations={canAnywhere(
										"organizations.read",
									)}
									value={
										audience === "personal"
											? PERSONAL_SCOPE
											: audience === "global"
												? null
												: audience
									}
									disabled={busy}
									onChange={(scope) => {
										const value =
											scope === PERSONAL_SCOPE
												? "personal"
												: scope == null
													? "global"
													: scope;
										setAudience(value);
										setSelected((keys) =>
											keys.filter((key) => {
												const resource =
													resourceMap.get(key);
												return (
													value === "personal" ||
													value === "global" ||
													!resource?.organization_id ||
													resource.organization_id ===
														value
												);
											}),
										);
									}}
								/>
								<p className="text-xs text-muted-foreground">
									Members see only resources they can access.
								</p>
							</div>
						)}
						<CollectionIconPicker value={icon} onChange={setIcon} />
						{selected.length > 0 && (
							<section className="space-y-2">
								<h3 className="text-sm font-medium">
									Selected items · {selected.length}
								</h3>
								<ol className="divide-y rounded border">
									{selected.map((key, index) => (
										<li
											key={key}
											className="flex min-w-0 items-center gap-1 px-2 py-1"
										>
											<CollectionResourceIcon
												resource={resourceMap.get(key)}
											/>
											<span className="min-w-0 flex-1 truncate text-sm">
												{resourceMap.get(key)?.name}
											</span>
											<Button
												type="button"
												size="icon"
												variant="ghost"
												className="size-11"
												disabled={index === 0 || busy}
												aria-label={`Move ${resourceMap.get(key)?.name} up`}
												onClick={() => move(index, -1)}
											>
												<ArrowUp className="size-4" />
											</Button>
											<Button
												type="button"
												size="icon"
												variant="ghost"
												className="size-11"
												disabled={
													index ===
														selected.length - 1 ||
													busy
												}
												aria-label={`Move ${resourceMap.get(key)?.name} down`}
												onClick={() => move(index, 1)}
											>
												<ArrowDown className="size-4" />
											</Button>
										</li>
									))}
								</ol>
							</section>
						)}
						<fieldset className="space-y-3">
							<legend className="text-sm font-medium">
								Add resources
							</legend>
							<Input
								aria-label="Search collection resources"
								placeholder="Search resources…"
								value={search}
								onChange={(event) =>
									setSearch(event.target.value)
								}
							/>
							<div className="rounded border">
								{matches.map((resource) => (
									<label
										key={resource.key}
										className="flex cursor-pointer items-center gap-3 border-b p-3 last:border-0"
									>
										<Checkbox
											checked={selected.includes(
												resource.key,
											)}
											disabled={
												busy ||
												(!selected.includes(
													resource.key,
												) &&
													selected.length >= 200)
											}
											onCheckedChange={(checked) =>
												setSelected((keys) =>
													checked
														? [
																...keys,
																resource.key,
															]
														: keys.filter(
																(key) =>
																	key !==
																	resource.key,
															),
												)
											}
										/>
										<CollectionResourceIcon
											resource={resource}
										/>
										<span className="min-w-0 text-sm">
											<span className="block [overflow-wrap:anywhere]">
												{resource.name}
											</span>
											<span className="text-xs text-muted-foreground">
												{resource.organization_name} ·{" "}
												{resource.kind}
											</span>
										</span>
									</label>
								))}
								{matches.length === 0 && (
									<p className="p-4 text-sm text-muted-foreground">
										No resources match this search and
										audience.
									</p>
								)}
							</div>
						</fieldset>
					</div>
					{error && (
						<p
							role="alert"
							className="shrink-0 px-5 pb-3 text-sm text-destructive sm:px-6"
						>
							{error}
						</p>
					)}
					{confirmDelete && (
						<p
							role="alert"
							className="shrink-0 px-5 pb-3 text-sm sm:px-6"
						>
							Delete this collection? Its apps, forms, and agents
							will remain available.
						</p>
					)}
					<DialogFooter className="shrink-0 gap-2 border-t px-5 py-4 sm:px-6">
						{collection && (
							<Button
								type="button"
								variant={
									confirmDelete ? "destructive" : "ghost"
								}
								className={
									confirmDelete
										? "min-h-11 sm:mr-auto"
										: "min-h-11 text-destructive hover:bg-destructive/10 hover:text-destructive sm:mr-auto"
								}
								disabled={busy}
								onClick={() =>
									confirmDelete
										? onDelete()
										: setConfirmDelete(true)
								}
							>
								<Trash2 className="size-4" />
								{confirmDelete ? "Confirm delete" : "Delete"}
							</Button>
						)}
						<div className="grid grid-cols-2 gap-2 sm:flex">
							<Button
								type="button"
								variant="outline"
								disabled={busy}
								onClick={onClose}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								disabled={busy || !name.trim() || !canSave}
							>
								{busy ? "Saving…" : "Save collection"}
							</Button>
						</div>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

function CollectionResourceIcon({ resource }: { resource?: HomeResource }) {
	if (!resource) return null;
	return (
		<ResourceIcon
			kind={resource.kind}
			id={resource.id}
			icon={resource.icon}
			logo={resource.logo_url ?? null}
			cacheKey={resource.logo_version ?? undefined}
			size="inline"
			className="mx-1"
		/>
	);
}
