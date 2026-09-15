import type { KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { Check, Pencil, Trash2 } from "lucide-react";
import { ResourceIcon } from "@/components/ResourceIcon";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import type { Integration } from "@/services/integrations";
import { IntegrationConnectionStatus } from "./IntegrationConnectionStatus";

interface IntegrationListProps {
	integrations: Integration[];
	isDesktop: boolean;
	isCardSelectMode: boolean;
	selectedIds: Set<string>;
	onToggleSelect: (id: string) => void;
	onToggleSelectAll: () => void;
	onOpen: (id: string) => void;
	onEdit: (id: string) => void;
	onDelete: (integration: Integration) => void;
}

function IntegrationActions({
	integration,
	onEdit,
	onDelete,
}: Pick<IntegrationListProps, "onEdit" | "onDelete"> & {
	integration: Integration;
}) {
	return (
		<RecordActionsMenu label={`${integration.name} actions`}>
			<DropdownMenuItem
				className="min-h-11"
				onSelect={() => onEdit(integration.id)}
			>
				<Pencil aria-hidden="true" className="size-4" />
				Edit
			</DropdownMenuItem>
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onSelect={() => onDelete(integration)}
			>
				<Trash2 aria-hidden="true" className="size-4" />
				Delete
			</DropdownMenuItem>
		</RecordActionsMenu>
	);
}

function IntegrationCard({
	integration,
	isCardSelectMode,
	selectedIds,
	onToggleSelect,
	onEdit,
	onDelete,
}: Pick<
	IntegrationListProps,
	| "isCardSelectMode"
	| "selectedIds"
	| "onToggleSelect"
	| "onEdit"
	| "onDelete"
> & { integration: Integration }) {
	const fields = integration.config_schema?.length ?? 0;
	const selected = selectedIds.has(integration.id);

	const handleCardToggle = () => {
		if (!isCardSelectMode) return;
		onToggleSelect(integration.id);
	};

	const handleCardKeyDown = (event: KeyboardEvent<HTMLElement>) => {
		if (!isCardSelectMode) return;
		if (event.target !== event.currentTarget) return;
		if (event.key !== "Enter" && event.key !== " ") return;
		event.preventDefault();
		onToggleSelect(integration.id);
	};

	return (
		<article
			data-testid="integration-card"
			role={isCardSelectMode ? "button" : undefined}
			tabIndex={isCardSelectMode ? 0 : undefined}
			aria-pressed={isCardSelectMode ? selected : undefined}
			aria-label={
				isCardSelectMode
					? `${selected ? "Deselect" : "Select"} ${integration.name}`
					: undefined
			}
			onClick={handleCardToggle}
			onKeyDown={handleCardKeyDown}
			className={`relative flex min-w-0 flex-col rounded-[var(--bf-radius-surface)] border bg-card transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
				selected && isCardSelectMode
					? "border-primary shadow-sm ring-1 ring-primary/40"
					: "border-border hover:border-primary/40 focus-within:border-primary/50"
			} ${isCardSelectMode ? "cursor-pointer" : ""}`}
		>
			{isCardSelectMode && (
				<div
					aria-hidden="true"
					className={`absolute right-4 top-4 z-10 flex size-6 items-center justify-center rounded-full border ${
						selected
							? "border-primary bg-primary text-primary-foreground"
							: "border-border bg-card text-transparent"
					}`}
				>
					<Check className="size-4" />
				</div>
			)}
			<div
				className={`flex min-w-0 items-start gap-3 p-5 ${isCardSelectMode ? "pr-14" : ""}`}
			>
				<ResourceIcon
					kind="integration"
					id={integration.id}
					logo={integration.logo_url ?? null}
					cacheKey={integration.logo_version ?? undefined}
				/>
				<div className="min-w-0 flex-1">
					<h2 className="min-w-0 text-base font-semibold [overflow-wrap:anywhere]">
						{isCardSelectMode ? (
							<span>{integration.name}</span>
						) : (
							<Link
								to={`/integrations/${integration.id}`}
								className="after:absolute after:inset-0 after:rounded-[var(--bf-radius-surface)] focus-visible:outline-none after:focus-visible:ring-2 after:focus-visible:ring-ring"
							>
								{integration.name}
							</Link>
						)}
					</h2>
					{integration.description && (
						<p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
							{integration.description}
						</p>
					)}
				</div>
				{!isCardSelectMode && (
					<div className="relative z-10 -mt-2 -mr-3 shrink-0">
						<IntegrationActions
							integration={integration}
							onEdit={onEdit}
							onDelete={onDelete}
						/>
					</div>
				)}
			</div>
			<dl className="mx-5 grid gap-3 border-t border-border py-4 text-sm">
				<div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
					<dt className="text-muted-foreground">Authentication</dt>
					<dd>
						{integration.has_oauth_config
							? "OAuth configured"
							: "No OAuth configuration"}
					</dd>
				</div>
				<div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
					<dt className="text-muted-foreground">
						Organization mappings
					</dt>
					<dd>{integration.mapping_count ?? 0}</dd>
				</div>
				<div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
					<dt className="text-muted-foreground">Connection status</dt>
					<dd>
						<IntegrationConnectionStatus
							integration={integration}
						/>
					</dd>
				</div>
			</dl>
			<div className="mt-auto flex items-center justify-between gap-3 border-t border-border px-5 py-4 text-sm text-muted-foreground">
				<span className="min-w-0">
					{fields} configuration {fields === 1 ? "field" : "fields"}
					{integration.list_entities_data_provider_id
						? " · Entity mapping available"
						: ""}
				</span>
			</div>
		</article>
	);
}

export function IntegrationList(props: IntegrationListProps) {
	const {
		integrations,
		isDesktop,
		isCardSelectMode,
		selectedIds,
		onToggleSelect,
		onToggleSelectAll,
		onOpen,
		onEdit,
		onDelete,
	} = props;
	const allSelected =
		integrations.length > 0 &&
		integrations.every((i) => selectedIds.has(i.id));
	const someSelected = integrations.some((i) => selectedIds.has(i.id));
	const selectAll = (
		<Checkbox
			aria-label="Select all visible integrations"
			checked={
				allSelected ? true : someSelected ? "indeterminate" : false
			}
			onCheckedChange={onToggleSelectAll}
		/>
	);
	if (!isDesktop)
		return (
			<div className="min-w-0 space-y-3">
				<div className="grid min-w-0 grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-4">
					{integrations.map((integration) => (
						<IntegrationCard
							key={integration.id}
							integration={integration}
							{...{
								selectedIds,
								isCardSelectMode,
								onToggleSelect,
								onEdit,
								onDelete,
							}}
						/>
					))}
				</div>
				<p className="py-2 text-sm text-muted-foreground">
					{integrations.length}{" "}
					{integrations.length === 1 ? "integration" : "integrations"}
				</p>
			</div>
		);
	return (
		<DataTable className="max-h-full">
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead className="w-12">{selectAll}</DataTableHead>
					<DataTableHead>Name</DataTableHead>
					<DataTableHead>Authentication</DataTableHead>
					<DataTableHead>Mappings</DataTableHead>
					<DataTableHead>Connection status</DataTableHead>
					<DataTableHead className="w-px text-right">
						Actions
					</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{integrations.map((integration) => (
					<DataTableRow
						key={integration.id}
						clickable
						href={`/integrations/${integration.id}`}
						onClick={() => onOpen(integration.id)}
					>
						<DataTableCell onClick={(e) => e.stopPropagation()}>
							<Checkbox
								aria-label={`Select ${integration.name}`}
								checked={selectedIds.has(integration.id)}
								onCheckedChange={() =>
									onToggleSelect(integration.id)
								}
							/>
						</DataTableCell>
						<DataTableCell>
							<div className="flex items-center gap-3">
								<ResourceIcon
									kind="integration"
									id={integration.id}
									size="table"
									logo={integration.logo_url ?? null}
									cacheKey={
										integration.logo_version ?? undefined
									}
								/>
								<Link
									onClick={(e) => e.stopPropagation()}
									to={`/integrations/${integration.id}`}
									className="font-medium hover:underline"
								>
									{integration.name}
								</Link>
							</div>
						</DataTableCell>
						<DataTableCell>
							{integration.has_oauth_config
								? "OAuth configured"
								: "No OAuth configuration"}
						</DataTableCell>
						<DataTableCell>
							{integration.mapping_count ?? 0}
						</DataTableCell>
						<DataTableCell>
							<IntegrationConnectionStatus
								integration={integration}
							/>
						</DataTableCell>
						<DataTableCell
							className="w-px text-right"
							onClick={(e) => e.stopPropagation()}
						>
							<IntegrationActions
								integration={integration}
								onEdit={onEdit}
								onDelete={onDelete}
							/>
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}
