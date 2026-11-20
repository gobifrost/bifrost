import {
	CheckCircle2,
	Link as LinkIcon,
	Settings,
	Unlink,
	PlugZap,
	RefreshCw,
	Loader2,
	Search,
	X,
	Check,
	Wand2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import {
	DropdownMenuItem,
	DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { PaginationFooter } from "@/components/pagination/PaginationFooter";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { ManualEntityIdInput } from "./ManualEntityIdInput";
import { type IntegrationMapping } from "@/services/integrations";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { WorkspacePrimaryAction } from "@/components/layout/WorkspacePrimaryAction";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { EntitySelector } from "@/components/integrations/EntitySelector";
import {
	isHighConfidence,
	MatchSuggestionActions,
} from "@/components/integrations/MatchSuggestionActions";
import type { MatchSuggestion, MatchResult } from "@/lib/matching";
import { isExpired } from "@/lib/client-types";
import {
	OrganizationTagList,
	TagFilter,
} from "@/components/organizations/OrganizationTags";
import {
	collectOrganizationTags,
	matchesTagFilter,
} from "@/lib/organization-tags";

interface MappingFormData {
	organization_id: string;
	entity_id: string;
	entity_name: string;
	oauth_token_id?: string;
	config: Record<string, unknown>;
}

export interface OrgWithMapping {
	id: string;
	name: string;
	tags?: string[] | null;
	mapping?: IntegrationMapping;
	formData: MappingFormData;
}

interface ConfigSchemaField {
	key: string;
	type: string;
	required?: boolean;
}

interface Entity {
	value: string;
	label: string;
}

type MappingStatus = "all" | "mapped" | "unmapped" | "suggested";

export type MappingAction = "connect" | "disconnect" | "refresh";

export interface PendingMappingAction {
	orgId: string;
	action: MappingAction;
}

export interface IntegrationMappingsTabProps {
	orgsWithMappings: OrgWithMapping[];
	entities: Entity[];
	isLoadingEntities: boolean;
	isEntitiesError?: boolean;
	isFetchingEntities?: boolean;
	onRetryEntities?: () => void;
	hasDataProvider: boolean;
	hasOAuth: boolean;
	configSchema: ConfigSchemaField[];
	configDefaults: Record<string, unknown> | null | undefined;
	autoMatchSuggestions: Map<string, MatchSuggestion>;
	matchStats: MatchResult["stats"] | null;
	isMatching: boolean;
	isDeletePending: boolean;
	onRunAutoMatch: () => void;
	onAcceptAllSuggestions: () => void;
	onClearSuggestions: () => void;
	onAcceptSuggestion: (orgId: string) => void;
	onRejectSuggestion: (orgId: string) => void;
	onUpdateOrgMapping: (
		orgId: string,
		entityId: string,
		entityName?: string,
	) => void;
	onOpenConfigDialog: (orgId: string) => void;
	onDeleteMapping: (org: OrgWithMapping) => void;
	onConnectMapping: (org: OrgWithMapping) => void;
	onDisconnectMapping: (org: OrgWithMapping) => void;
	onRefreshMapping: (org: OrgWithMapping) => void;
	pendingMappingAction?: PendingMappingAction | null;
	canManageMapping: (organizationId: string) => boolean;
	canManageAllMappings: boolean;
}

export function IntegrationMappingsTab({
	orgsWithMappings,
	entities,
	isLoadingEntities,
	isEntitiesError,
	isFetchingEntities = false,
	onRetryEntities,
	hasDataProvider,
	hasOAuth,
	configSchema,
	configDefaults,
	autoMatchSuggestions,
	matchStats,
	isMatching,
	isDeletePending,
	onRunAutoMatch,
	onAcceptAllSuggestions,
	onClearSuggestions,
	onAcceptSuggestion,
	onRejectSuggestion,
	onUpdateOrgMapping,
	onOpenConfigDialog,
	onDeleteMapping,
	onConnectMapping,
	onDisconnectMapping,
	onRefreshMapping,
	pendingMappingAction = null,
	canManageMapping,
	canManageAllMappings,
}: IntegrationMappingsTabProps) {
	const hasNonDefaultConfig = (org: OrgWithMapping): boolean => {
		if (!org.mapping?.config || !configSchema) return false;

		const defaults = configDefaults ?? {};

		return configSchema.some((field) => {
			const currentValue = org.mapping?.config?.[field.key];
			const defaultValue = defaults[field.key];
			return currentValue !== defaultValue;
		});
	};

	const isActionPending = pendingMappingAction !== null;
	const [mappingSearch, setMappingSearch] = useState("");
	const [mappingStatus, setMappingStatus] = useState<MappingStatus>("all");
	const [tagFilter, setTagFilter] = useState<string[]>([]);
	const knownTags = useMemo(
		() => collectOrganizationTags(orgsWithMappings),
		[orgsWithMappings],
	);
	const [offset, setOffset] = useState(0);
	const pageSize = 25;
	const usedEntityIds = useMemo(
		() =>
			new Set(
				orgsWithMappings
					.map((org) => org.formData.entity_id)
					.filter(Boolean),
			),
		[orgsWithMappings],
	);
	const unusedEntities = useMemo(
		() => entities.filter((entity) => !usedEntityIds.has(entity.value)),
		[entities, usedEntityIds],
	);
	const entityLabelByValue = useMemo(
		() => new Map(entities.map((entity) => [entity.value, entity.label])),
		[entities],
	);
	const normalizedSearch = mappingSearch.trim().toLocaleLowerCase();
	const hasFilters =
		normalizedSearch.length > 0 ||
		(mappingStatus !== "all" && mappingStatus !== "suggested") ||
		tagFilter.length > 0;
	const clearFilters = () => {
		setMappingSearch("");
		setMappingStatus("all");
		setTagFilter([]);
		setOffset(0);
	};
	const suggestionCount = autoMatchSuggestions.size;
	// Running auto-match opens the Suggested view; it closes when review ends.
	const [seenSuggestionCount, setSeenSuggestionCount] =
		useState(suggestionCount);
	if (seenSuggestionCount !== suggestionCount) {
		setSeenSuggestionCount(suggestionCount);
		if (seenSuggestionCount === 0 && suggestionCount > 0) {
			setMappingStatus("suggested");
			setOffset(0);
		} else if (suggestionCount === 0 && mappingStatus === "suggested") {
			setMappingStatus("all");
			setOffset(0);
		}
	}
	const activeStatus: MappingStatus =
		mappingStatus === "suggested" && suggestionCount === 0
			? "all"
			: mappingStatus;
	const searchedOrgs = useMemo(() => {
		return orgsWithMappings.filter((org) => {
			if (!matchesTagFilter(org, tagFilter)) return false;
			if (!normalizedSearch) return true;
			const entityId =
				org.formData.entity_id || org.mapping?.entity_id || "";
			const searchable = [
				org.name,
				...(org.tags ?? []),
				org.formData.entity_name,
				org.mapping?.entity_name,
				entityId,
				entityLabelByValue.get(entityId),
			]
				.filter(Boolean)
				.join(" ")
				.toLocaleLowerCase();

			return searchable.includes(normalizedSearch);
		});
	}, [entityLabelByValue, normalizedSearch, orgsWithMappings, tagFilter]);
	const statusCounts = useMemo(
		() => ({
			all: searchedOrgs.length,
			mapped: searchedOrgs.filter((org) => org.mapping).length,
			unmapped: searchedOrgs.filter((org) => !org.mapping).length,
			suggested: searchedOrgs.filter((org) =>
				autoMatchSuggestions.has(org.id),
			).length,
		}),
		[searchedOrgs, autoMatchSuggestions],
	);
	const filteredOrgs = useMemo(
		() =>
			searchedOrgs.filter((org) => {
				if (activeStatus === "mapped") return Boolean(org.mapping);
				if (activeStatus === "unmapped") return !org.mapping;
				if (activeStatus === "suggested")
					return autoMatchSuggestions.has(org.id);
				return true;
			}),
		[searchedOrgs, activeStatus, autoMatchSuggestions],
	);
	const pageOffset = Math.min(
		offset,
		Math.max(0, Math.ceil(filteredOrgs.length / pageSize) - 1) * pageSize,
	);
	const pageOrgs = useMemo(
		() => filteredOrgs.slice(pageOffset, pageOffset + pageSize),
		[filteredOrgs, pageOffset],
	);
	const availableEntitiesByOrg = useMemo(
		() =>
			new Map(
				pageOrgs.map((org) => [
					org.id,
					org.formData.entity_id
						? entities.filter(
								(entity) =>
									entity.value === org.formData.entity_id ||
									!usedEntityIds.has(entity.value),
							)
						: unusedEntities,
				]),
			),
		[pageOrgs, entities, usedEntityIds, unusedEntities],
	);

	const showAutoMatch =
		hasDataProvider && canManageAllMappings && orgsWithMappings.length > 0;
	const totalPages = Math.max(1, Math.ceil(filteredOrgs.length / pageSize));
	const countLabel =
		normalizedSearch || filteredOrgs.length !== orgsWithMappings.length
			? `${filteredOrgs.length} of ${orgsWithMappings.length} mappings`
			: `${orgsWithMappings.length} mappings`;
	const statusOptions: { value: MappingStatus; label: string }[] = [
		{ value: "all", label: "All Mappings" },
		{ value: "mapped", label: "Mapped" },
		{ value: "unmapped", label: "Unmapped" },
		...(suggestionCount > 0
			? [{ value: "suggested" as const, label: "Suggested" }]
			: []),
	];
	const entityControlsDisabled =
		isLoadingEntities || Boolean(isEntitiesError) || isFetchingEntities;

	return (
		<section
			aria-label="Organization Mappings"
			className="overflow-clip rounded-[var(--bf-radius-feature)] border border-border/70 bg-card"
		>
			{/*
			 * Sticky so status, search, and auto-match stay reachable while the
			 * page scrolls. `overflow-clip` keeps the rounded corners without
			 * creating a scroll container that would defeat sticking.
			 */}
			{orgsWithMappings.length > 0 && (
				<div className="sticky top-0 z-20 bg-card">
					<ListToolbar className="gap-0 border-b border-border/70 bg-muted/20 p-0">
						<div className="w-full shrink-0 border-b border-border/70 sm:w-44 sm:self-stretch xl:w-56 sm:border-b-0 sm:border-r">
							<Select
								value={activeStatus}
								onValueChange={(value) => {
									setMappingStatus(value as MappingStatus);
									setOffset(0);
								}}
							>
								<SelectTrigger
									aria-label="Mapping Status"
									className="h-full min-h-12 w-full rounded-none border-0 bg-transparent px-4 py-2 shadow-none hover:bg-muted/50 focus-visible:ring-inset"
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{statusOptions.map((option) => (
										<SelectItem
											key={option.value}
											value={option.value}
										>
											{option.label}
											<span className="text-xs tabular-nums text-muted-foreground">
												{statusCounts[option.value]}
											</span>
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>
						<div className="flex min-w-0 flex-1 flex-wrap items-center">
							<div className="relative min-w-0 w-full max-sm:m-3 max-sm:w-[calc(100%-1.5rem)] sm:min-w-40 sm:flex-1">
								<Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
								<Input
									type="search"
									value={mappingSearch}
									onChange={(event) => {
										setMappingSearch(event.target.value);
										setOffset(0);
									}}
									aria-label="Search organization mappings"
									placeholder="Search names, IDs, or tags..."
									className="h-10 pl-9 pr-10 sm:h-12 sm:rounded-none sm:border-0 sm:bg-transparent sm:shadow-none sm:focus-visible:ring-inset md:h-12"
								/>
								{mappingSearch.trim().length > 0 && (
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="absolute right-1 top-1/2 size-8 -translate-y-1/2"
										aria-label="Clear mapping search"
										onClick={() => {
											setMappingSearch("");
											setOffset(0);
										}}
									>
										<X className="size-4" />
									</Button>
								)}
							</div>
							<div className="flex min-w-0 items-stretch self-stretch max-sm:w-full max-sm:border-t max-sm:border-border/70 sm:ml-auto sm:border-l sm:border-border/70">
								<TagFilter
									tags={knownTags}
									value={tagFilter}
									onChange={(value) => {
										setTagFilter(value);
										setOffset(0);
									}}
									className="h-auto min-h-12 flex-1 rounded-none border-0 bg-transparent px-4 shadow-none hover:bg-muted/50 focus-visible:ring-inset sm:w-36 sm:flex-none lg:min-h-12 xl:w-44"
								/>
								{showAutoMatch && (
									<WorkspacePrimaryAction
										onClick={() => onRunAutoMatch()}
										disabled={
											entityControlsDisabled || isMatching
										}
									>
										<Wand2 className="size-4" />
										{isMatching
											? "Matching..."
											: "Auto-Match"}
									</WorkspacePrimaryAction>
								)}
							</div>
						</div>
					</ListToolbar>
				</div>
			)}
			{!hasDataProvider && orgsWithMappings.length > 0 && (
				<p className="border-b border-border/70 px-4 py-2 text-sm text-muted-foreground">
					Auto-match is unavailable without a data provider. Enter
					entity IDs manually.
				</p>
			)}
			{isEntitiesError && (
				<div
					role="alert"
					className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 bg-[var(--bf-warning-soft)] px-4 py-2 text-sm text-[var(--bf-warning)]"
				>
					<p>
						Unable to load external entities. Retry to select or
						match organizations.
					</p>
					{onRetryEntities && (
						<Button
							type="button"
							variant="outline"
							size="sm"
							className="h-9"
							disabled={isFetchingEntities}
							onClick={onRetryEntities}
						>
							{isFetchingEntities
								? "Retrying…"
								: "Retry entities"}
						</Button>
					)}
				</div>
			)}
			{activeStatus === "suggested" && (
				<div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/70 bg-muted/20 px-4 py-2">
					<p className="min-w-0 basis-full text-sm sm:basis-0 sm:flex-1">
						<Wand2 className="mr-2 inline size-4 text-primary" />
						{suggestionCount === 1
							? "1 suggested match."
							: `${suggestionCount} suggested matches.`}{" "}
						<span className="text-muted-foreground">
							Accept or reject each row, or accept them all.
						</span>
					</p>
					<div className="flex items-center gap-2">
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-9"
							onClick={onClearSuggestions}
							disabled={entityControlsDisabled}
						>
							Clear
						</Button>
						<Button
							type="button"
							size="sm"
							className="h-9"
							onClick={onAcceptAllSuggestions}
							disabled={entityControlsDisabled}
						>
							<Check className="size-4" />
							Accept All ({matchStats?.matched ?? suggestionCount}
							)
						</Button>
					</div>
				</div>
			)}
			{orgsWithMappings.length === 0 ? (
				<div className="flex flex-col items-center justify-center px-4 py-12 text-center">
					<LinkIcon className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						No organizations available
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						Create organizations first to set up mappings
					</p>
				</div>
			) : filteredOrgs.length === 0 ? (
				<div className="px-4 py-10 text-center">
					<h3 className="text-base font-semibold">
						No mappings match your search
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						Try another organization, external entity, or tag.
					</p>
				</div>
			) : (
				<ul
					className="divide-y divide-border/70"
					aria-label="Organization mappings"
				>
					{pageOrgs.map((org) => (
						<IntegrationMappingRecord
							key={org.id}
							org={org}
							availableEntities={availableEntitiesByOrg.get(
								org.id,
							)!}
							hasNonDefaultConfig={hasNonDefaultConfig(org)}
							hasDataProvider={hasDataProvider}
							autoMatchSuggestions={autoMatchSuggestions}
							onAcceptSuggestion={onAcceptSuggestion}
							onRejectSuggestion={onRejectSuggestion}
							onUpdateOrgMapping={onUpdateOrgMapping}
							isLoadingEntities={isLoadingEntities}
							isEntitiesError={isEntitiesError}
							hasOAuth={hasOAuth}
							onRefreshMapping={onRefreshMapping}
							onConnectMapping={onConnectMapping}
							onOpenConfigDialog={onOpenConfigDialog}
							onDisconnectMapping={onDisconnectMapping}
							onDeleteMapping={onDeleteMapping}
							isDeletePending={isDeletePending}
							pendingMappingAction={pendingMappingAction}
							isActionPending={isActionPending}
							canManage={canManageMapping(org.id)}
							reviewing={suggestionCount > 0}
						/>
					))}
				</ul>
			)}
			{orgsWithMappings.length > 0 && (
				<PaginationFooter
					aria-label="Mapping pagination"
					className="border-t border-border/70 px-4 !py-2"
					summary={
						<span className="flex flex-wrap items-center gap-x-3 gap-y-1">
							<span>{countLabel}</span>
							{totalPages > 1 && (
								<span>
									Page {pageOffset / pageSize + 1} of{" "}
									{totalPages}
								</span>
							)}
							{hasFilters && (
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="h-8"
									onClick={clearFilters}
								>
									Clear Filters
								</Button>
							)}
						</span>
					}
					showControls={totalPages > 1}
					previousDisabled={pageOffset === 0}
					nextDisabled={pageOffset / pageSize + 1 >= totalPages}
					onPrevious={() =>
						setOffset(Math.max(0, pageOffset - pageSize))
					}
					onNext={() => setOffset(pageOffset + pageSize)}
				/>
			)}
		</section>
	);
}

function formatTimeUntil(expiresAt: string | null): string {
	if (!expiresAt) return "Expiry unknown";
	const ms = new Date(expiresAt).getTime() - Date.now();
	if (Number.isNaN(ms)) return "Expiry unknown";
	if (ms <= 0) return "Expired";
	const totalMinutes = Math.floor(ms / 60000);
	const days = Math.floor(totalMinutes / (60 * 24));
	const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
	const minutes = totalMinutes % 60;
	if (days > 0) return `Expires in ${days}d ${hours}h`;
	if (hours > 0) return `Expires in ${hours}h ${minutes}m`;
	return `Expires in ${minutes}m`;
}

function getConnectionState(org: OrgWithMapping) {
	const mapping = org.mapping;
	if (!mapping) return "none";
	if (mapping.connection_status === "failed") return "failed";
	if (mapping.connection_status === "expired") return "expired";
	if (mapping.connection_status === "completed") {
		if (
			mapping.connection_expires_at &&
			isExpired(mapping.connection_expires_at)
		) {
			return "expired";
		}
		return "connected";
	}
	return "none";
}

function getConnectionActionLabel(
	state: ReturnType<typeof getConnectionState>,
) {
	if (state === "failed") return "Retry connect";
	if (state === "expired") return "Reconnect";
	return "Connect";
}

function getConnectionActionPendingLabel(
	state: ReturnType<typeof getConnectionState>,
) {
	if (state === "failed") return "Retrying…";
	if (state === "expired") return "Reconnecting…";
	return "Connecting…";
}

function IntegrationMappingRecord({
	org,
	availableEntities,
	hasNonDefaultConfig,
	hasDataProvider,
	autoMatchSuggestions,
	onAcceptSuggestion,
	onRejectSuggestion,
	onUpdateOrgMapping,
	isLoadingEntities,
	isEntitiesError,
	hasOAuth,
	onRefreshMapping,
	onConnectMapping,
	onOpenConfigDialog,
	onDisconnectMapping,
	onDeleteMapping,
	isDeletePending,
	pendingMappingAction,
	isActionPending,
	canManage,
	reviewing,
}: Pick<
	IntegrationMappingsTabProps,
	| "hasDataProvider"
	| "autoMatchSuggestions"
	| "onAcceptSuggestion"
	| "onRejectSuggestion"
	| "onUpdateOrgMapping"
	| "isLoadingEntities"
	| "isEntitiesError"
	| "hasOAuth"
	| "onRefreshMapping"
	| "onConnectMapping"
	| "onOpenConfigDialog"
	| "onDisconnectMapping"
	| "onDeleteMapping"
	| "isDeletePending"
	| "pendingMappingAction"
> & {
	org: OrgWithMapping;
	availableEntities: Entity[];
	hasNonDefaultConfig: boolean;
	isActionPending: boolean;
	canManage: boolean;
	reviewing: boolean;
}) {
	const connectionState = getConnectionState(org);
	const isConnectPending =
		pendingMappingAction?.orgId === org.id &&
		pendingMappingAction.action === "connect";
	const isDisconnectPending =
		pendingMappingAction?.orgId === org.id &&
		pendingMappingAction.action === "disconnect";
	const isRefreshPending =
		pendingMappingAction?.orgId === org.id &&
		pendingMappingAction.action === "refresh";
	const isRowLocked = !canManage || isActionPending || isDeletePending;

	const suggestion = autoMatchSuggestions.get(org.id);

	return (
		/*
		 * Name and a details line on the left; the entity picker, review
		 * actions, and menu sit in fixed slots on the right so they align on
		 * every row. Phones stack the picker under the name.
		 */
		<li
			className={cn(
				"grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-4 py-3",
				reviewing
					? "lg:grid-cols-[minmax(10rem,1fr)_minmax(14rem,1fr)_7rem_2.25rem]"
					: "lg:grid-cols-[minmax(10rem,1fr)_minmax(14rem,1fr)_2.25rem]",
			)}
		>
			<div className="min-w-0">
				<h3 className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">
					{org.name}
				</h3>
				<div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
					{suggestion ? (
						<span className="font-medium text-primary">
							Suggested Match
						</span>
					) : org.mapping ? (
						<span className="inline-flex items-center gap-1 font-medium text-[var(--bf-success)]">
							<CheckCircle2 className="size-3" />
							<span>Mapped</span>
						</span>
					) : org.formData.entity_id ? (
						<span className="font-medium text-foreground">New</span>
					) : (
						<span>Not Mapped</span>
					)}
					{hasOAuth && (
						<>
							<span aria-hidden="true">·</span>
							{connectionState === "connected" ? (
								<span>
									<span className="font-medium text-[var(--bf-success)]">
										Connected
									</span>{" "}
									{formatTimeUntil(
										org.mapping?.connection_expires_at ??
											null,
									).toLocaleLowerCase()}
								</span>
							) : connectionState === "failed" ? (
								<span className="text-destructive [overflow-wrap:anywhere]">
									<span className="font-medium">Failed</span>
									{org.mapping?.connection_message && (
										<>: {org.mapping.connection_message}</>
									)}
								</span>
							) : connectionState === "expired" ? (
								<span className="text-destructive">
									<span className="font-medium">Expired</span>{" "}
									- reconnect required
								</span>
							) : (
								<span>Not connected</span>
							)}
						</>
					)}
					{!canManage && (
						<>
							<span aria-hidden="true">·</span>
							<span>Read Only</span>
						</>
					)}
				</div>
				<OrganizationTagList tags={org.tags} className="mt-1.5" />
			</div>
			<div className="col-start-2 row-start-1 flex justify-end lg:order-last lg:col-start-auto lg:row-start-auto">
				{canManage && (
					<RecordActionsMenu
						label={`Mapping actions for ${org.name}`}
					>
						<DropdownMenuItem
							disabled={isRowLocked}
							onSelect={() => onOpenConfigDialog(org.id)}
						>
							<Settings className="size-4" /> Configure
							{hasNonDefaultConfig && (
								<span
									className="ml-auto size-2 shrink-0 rounded-full bg-primary"
									aria-label="Custom configuration"
								/>
							)}
						</DropdownMenuItem>
						{hasOAuth && connectionState !== "connected" && (
							<DropdownMenuItem
								disabled={isRowLocked || isConnectPending}
								onSelect={() => onConnectMapping(org)}
							>
								{isConnectPending ? (
									<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
								) : (
									<LinkIcon className="size-4" />
								)}
								{isConnectPending
									? getConnectionActionPendingLabel(
											connectionState,
										)
									: getConnectionActionLabel(connectionState)}
							</DropdownMenuItem>
						)}
						{hasOAuth && connectionState === "connected" && (
							<DropdownMenuItem
								disabled={isRowLocked || isRefreshPending}
								onSelect={() => onRefreshMapping(org)}
							>
								{isRefreshPending ? (
									<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
								) : (
									<RefreshCw className="size-4" />
								)}
								{isRefreshPending
									? "Refreshing…"
									: "Refresh token"}
							</DropdownMenuItem>
						)}
						{org.mapping?.oauth_token_id && (
							<DropdownMenuItem
								disabled={isRowLocked || isDisconnectPending}
								onSelect={() => onDisconnectMapping(org)}
							>
								<PlugZap className="size-4" />
								{isDisconnectPending
									? "Disconnecting…"
									: "Disconnect"}
							</DropdownMenuItem>
						)}
						<DropdownMenuSeparator />
						<DropdownMenuItem
							variant="destructive"
							disabled={!org.mapping || isRowLocked}
							onSelect={() => onDeleteMapping(org)}
						>
							<Unlink className="size-4" /> Unlink
						</DropdownMenuItem>
					</RecordActionsMenu>
				)}
			</div>
			<fieldset
				disabled={isRowLocked}
				className="col-span-2 min-w-0 lg:col-span-1"
			>
				{!hasDataProvider ? (
					<ManualEntityIdInput
						orgId={org.id}
						value={org.formData.entity_id}
						onCommit={onUpdateOrgMapping}
						disabled={!canManage}
					/>
				) : (
					<EntitySelector
						aria-label={`External entity for ${org.name}`}
						entities={availableEntities}
						value={suggestion?.entityId ?? org.formData.entity_id}
						onChange={(value, label) => {
							if (suggestion) {
								onRejectSuggestion(org.id);
								if (!value) return;
							}
							onUpdateOrgMapping(org.id, value, label);
						}}
						isLoading={isLoadingEntities}
						isError={isEntitiesError}
						disabled={!canManage}
						placeholder="Select entity..."
						className={cn(
							suggestion &&
								(isHighConfidence(suggestion)
									? "border-[var(--bf-success)]/50 bg-[var(--bf-success-soft)]"
									: "border-[var(--bf-warning)]/50 bg-[var(--bf-warning-soft)]"),
						)}
					/>
				)}
			</fieldset>
			{reviewing && (
				<div
					className={cn(
						"col-span-2 lg:col-span-1",
						!suggestion && "hidden lg:block",
					)}
				>
					{suggestion && (
						<MatchSuggestionActions
							suggestion={suggestion}
							onAccept={() => onAcceptSuggestion(org.id)}
							onReject={() => onRejectSuggestion(org.id)}
							disabled={isRowLocked}
						/>
					)}
				</div>
			)}
		</li>
	);
}
