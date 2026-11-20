import { useState } from "react";
import { ChevronsUpDown, Tag } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
	CommandSeparator,
} from "@/components/ui/command";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { TagsInput } from "@/components/ui/tags-input";
import { useBulkUpdateOrganizationTags } from "@/hooks/useOrganizations";
import { getErrorMessage } from "@/lib/api-error";
import { collectOrganizationTags } from "@/lib/organization-tags";
import { cn } from "@/lib/utils";

/** An organization's tags as compact badges; extra tags collapse to "+N". */
export function OrganizationTagList({
	tags,
	max = 3,
	className,
}: {
	tags?: string[] | null;
	max?: number;
	className?: string;
}) {
	const list = tags ?? [];
	if (list.length === 0) return null;
	const shown = list.slice(0, max);
	const hidden = list.slice(max);
	return (
		<div className={cn("flex min-w-0 flex-wrap gap-1", className)}>
			{shown.map((tag) => (
				<Badge
					key={tag}
					variant="secondary"
					className="max-w-full [overflow-wrap:anywhere]"
				>
					{tag}
				</Badge>
			))}
			{hidden.length > 0 && (
				<Badge
					variant="outline"

					title={hidden.join(", ")}
					aria-label={`${hidden.length} more tags: ${hidden.join(", ")}`}
				>
					+{hidden.length}
				</Badge>
			)}
		</div>
	);
}

/**
 * Multi-select tag filter. An item matches when it carries any selected tag.
 */
export function TagFilter({
	tags,
	value,
	onChange,
	className,
}: {
	tags: string[];
	value: string[];
	onChange: (tags: string[]) => void;
	className?: string;
}) {
	const [open, setOpen] = useState(false);
	const selected = new Set(value.map((tag) => tag.toLocaleLowerCase()));
	const toggle = (tag: string) =>
		onChange(
			selected.has(tag.toLocaleLowerCase())
				? value.filter(
						(v) =>
							v.toLocaleLowerCase() !== tag.toLocaleLowerCase(),
					)
				: [...value, tag],
		);
	const label =
		value.length === 0
			? "All Tags"
			: value.length <= 2
				? value.join(", ")
				: `${value.length} Tags`;

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="outline"
					role="combobox"
					aria-expanded={open}
					aria-label={
						value.length
							? `Filter by tag: ${value.join(", ")}`
							: "Filter by tag"
					}
					className={cn(
						"min-h-11 justify-between gap-2 lg:min-h-10",
						className,
					)}
					disabled={tags.length === 0 && value.length === 0}
				>
					<Tag className="h-4 w-4 shrink-0" />
					<span className="truncate">{label}</span>
					<ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
				</Button>
			</PopoverTrigger>
			<PopoverContent variant="picker" className="p-0" align="start">
				<Command>
					<CommandInput placeholder="Search tags..." />
					<CommandList className="max-h-60 overflow-y-auto">
						<CommandEmpty>No tags found.</CommandEmpty>
						<CommandGroup>
							{tags.map((tag) => (
								<CommandItem
									key={tag}
									value={tag}
									className="min-h-11 lg:min-h-9"
									onSelect={() => toggle(tag)}
								>
									<Checkbox
										checked={selected.has(
											tag.toLocaleLowerCase(),
										)}
										aria-hidden
										tabIndex={-1}
										className="pointer-events-none"
									/>
									<span className="[overflow-wrap:anywhere]">
										{tag}
									</span>
								</CommandItem>
							))}
						</CommandGroup>
						{value.length > 0 && (
							<>
								<CommandSeparator />
								<CommandGroup>
									<CommandItem
										value="__clear_tag_filter__"
										className="min-h-11 justify-center lg:min-h-9"
										onSelect={() => onChange([])}
									>
										Clear Tag Filter
									</CommandItem>
								</CommandGroup>
							</>
						)}
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}

export type BulkTagMode = "add" | "remove";

interface BulkTagOrganization {
	id: string;
	name: string;
	tags?: string[] | null;
}

/** Add or remove tags on the selected organizations in one request. */
export function BulkTagDialog({
	mode,
	organizations,
	knownTags,
	onOpenChange,
	onSuccess,
}: {
	/** Null closes the dialog. */
	mode: BulkTagMode | null;
	organizations: BulkTagOrganization[];
	/** Every tag in use, offered as suggestions when adding. */
	knownTags: string[];
	onOpenChange: (open: boolean) => void;
	onSuccess: () => void;
}) {
	const [tags, setTags] = useState<string[]>([]);
	const mutation = useBulkUpdateOrganizationTags();
	const open = mode !== null;
	const count = organizations.length;
	const noun = count === 1 ? "organization" : "organizations";
	const selectedTags = collectOrganizationTags(organizations);
	const chosen = new Set(tags.map((tag) => tag.toLocaleLowerCase()));
	const suggestions = (mode === "remove" ? selectedTags : knownTags).filter(
		(tag) => !chosen.has(tag.toLocaleLowerCase()),
	);

	const close = (next: boolean) => {
		if (mutation.isPending) return;
		if (!next) {
			setTags([]);
			mutation.reset();
		}
		onOpenChange(next);
	};

	const submit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!mode || tags.length === 0 || mutation.isPending) return;
		try {
			await mutation.mutateAsync({
				body: {
					organization_ids: organizations.map((org) => org.id),
					add: mode === "add" ? tags : [],
					remove: mode === "remove" ? tags : [],
				},
			});
			toast.success(
				mode === "add"
					? `Added ${tags.length === 1 ? "tag" : "tags"} to ${count} ${noun}`
					: `Removed ${tags.length === 1 ? "tag" : "tags"} from ${count} ${noun}`,
			);
			setTags([]);
			mutation.reset();
			onSuccess();
		} catch {
			// Shown inline below.
		}
	};

	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogContent className="sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>
						{mode === "remove" ? "Remove Tags" : "Add Tags"}
					</DialogTitle>
					<DialogDescription>
						{mode === "remove"
							? `Remove tags from ${count} selected ${noun}.`
							: `Add tags to ${count} selected ${noun}. Existing tags are kept.`}
					</DialogDescription>
				</DialogHeader>
				<form
					id="bulk-organization-tags-form"
					onSubmit={submit}
					className="space-y-4"
				>
					{mode === "add" ? (
						<div className="space-y-2">
							<Label htmlFor="bulk-organization-tags">Tags</Label>
							<TagsInput
								id="bulk-organization-tags"
								value={tags}
								onChange={setTags}
								placeholder="Type a tag and press Enter..."
								aria-label="Tags to add"
							/>
						</div>
					) : (
						tags.length > 0 && (
							<div className="space-y-2">
								<Label>Tags to Remove</Label>
								<div className="flex flex-wrap gap-2">
									{tags.map((tag) => (
										<Button
											key={tag}
											type="button"
											variant="secondary"
											size="sm"
											className="min-h-11 sm:min-h-8"
											aria-label={`Keep ${tag}`}
											onClick={() =>
												setTags(
													tags.filter(
														(t) => t !== tag,
													),
												)
											}
										>
											{tag} ×
										</Button>
									))}
								</div>
							</div>
						)
					)}
					{suggestions.length > 0 && (
						<div className="space-y-2">
							<p className="text-sm text-muted-foreground">
								{mode === "remove"
									? "Tags on the selected organizations"
									: "Existing tags"}
							</p>
							<div className="flex max-h-40 flex-wrap gap-2 overflow-auto">
								{suggestions.map((tag) => (
									<Button
										key={tag}
										type="button"
										variant="outline"
										size="sm"
										className="min-h-11 sm:min-h-8"
										onClick={() => setTags([...tags, tag])}
									>
										{tag}
									</Button>
								))}
							</div>
						</div>
					)}
					{mode === "remove" && selectedTags.length === 0 && (
						<p className="text-sm text-muted-foreground">
							The selected organizations have no tags.
						</p>
					)}
					{mutation.error && (
						<Alert variant="destructive">
							<AlertDescription>
								{getErrorMessage(
									mutation.error,
									"Could not update tags",
								)}{" "}
								Nothing was changed.
							</AlertDescription>
						</Alert>
					)}
				</form>
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => close(false)}
						disabled={mutation.isPending}
					>
						Cancel
					</Button>
					<Button
						type="submit"
						form="bulk-organization-tags-form"
						variant={mode === "remove" ? "destructive" : "default"}
						disabled={tags.length === 0 || mutation.isPending}
					>
						{mutation.isPending
							? "Saving..."
							: mode === "remove"
								? "Remove Tags"
								: "Add Tags"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
