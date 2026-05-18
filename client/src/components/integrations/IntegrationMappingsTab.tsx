import {
	CheckCircle2,
	Plus,
	Link as LinkIcon,
	Settings,
	Unlink,
	PlugZap,
	RefreshCw,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { type IntegrationMapping } from "@/services/integrations";
import { AutoMatchControls } from "@/components/integrations/AutoMatchControls";
import { EntitySelector } from "@/components/integrations/EntitySelector";
import { MatchSuggestionBadge } from "@/components/integrations/MatchSuggestionBadge";
import type { MatchMode, MatchSuggestion, MatchResult } from "@/lib/matching";

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

export interface IntegrationMappingsTabProps {
	orgsWithMappings: OrgWithMapping[];
	entities: Entity[];
	isLoadingEntities: boolean;
	isEntitiesError?: boolean;
	hasDataProvider: boolean;
	hasOAuth: boolean;
	configSchema: ConfigSchemaField[];
	configDefaults: Record<string, unknown> | null | undefined;
	autoMatchSuggestions: Map<string, MatchSuggestion>;
	matchStats: MatchResult["stats"] | null;
	isMatching: boolean;
	isDeletePending: boolean;
	onRunAutoMatch: (mode: MatchMode) => void;
	onAcceptAllSuggestions: () => void;
	onClearSuggestions: () => void;
	onAcceptSuggestion: (orgId: string) => void;
	onRejectSuggestion: (orgId: string) => void;
	onUpdateOrgMapping: (orgId: string, entityId: string, entityName?: string) => void;
	onOpenConfigDialog: (orgId: string) => void;
	onDeleteMapping: (org: OrgWithMapping) => void;
	onConnectMapping: (org: OrgWithMapping) => void;
	onDisconnectMapping: (mappingId: string) => void;
	onRefreshMapping: (mappingId: string) => void;
	refreshingMappingId?: string | null;
}

export function IntegrationMappingsTab({
	orgsWithMappings,
	entities,
	isLoadingEntities,
	isEntitiesError,
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
	refreshingMappingId = null,
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

	return (
		<Card>
			<CardHeader className="flex flex-row items-start justify-between space-y-0">
				<div>
					<CardTitle>Organization Mappings</CardTitle>
					<CardDescription>
						Configure how each organization maps to
						external entities
					</CardDescription>
				</div>
				{/* Auto-Match Controls in header */}
				{hasDataProvider &&
					orgsWithMappings.length > 0 && (
						<AutoMatchControls
							onRunAutoMatch={onRunAutoMatch}
							onAcceptAll={onAcceptAllSuggestions}
							onClear={onClearSuggestions}
							matchStats={matchStats}
							hasSuggestions={
								autoMatchSuggestions.size > 0
							}
							isMatching={isMatching}
							disabled={isLoadingEntities}
						/>
					)}
			</CardHeader>
			<CardContent>
				{orgsWithMappings.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12 text-center">
						<LinkIcon className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							No organizations available
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							Create organizations first to set up
							mappings
						</p>
					</div>
				) : (
					<>
						{!hasDataProvider && (
							<p className="text-sm text-muted-foreground mb-4">
								No data provider configured — entity IDs must be entered manually.
							</p>
						)}
						<div className="rounded-md border overflow-x-auto">
							<DataTable>
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead className="w-48">
											Organization
										</DataTableHead>
										<DataTableHead className="w-64">
											External Entity
										</DataTableHead>
										<DataTableHead className="w-24">
											Status
										</DataTableHead>
										<DataTableHead className="w-32">
											Connection
										</DataTableHead>
										<DataTableHead className="w-32 text-right">
											Actions
										</DataTableHead>
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{orgsWithMappings.map((org) => {
										// Filter out entities already mapped to other orgs
										const usedEntityIds =
											orgsWithMappings
												.filter(
													(o) =>
														o.id !==
															org.id &&
														o.formData
															.entity_id,
												)
												.map(
													(o) =>
														o.formData
															.entity_id,
												);
										const availableEntities =
											entities.filter(
												(e) =>
													e.value ===
														org.formData
															.entity_id ||
													!usedEntityIds.includes(
														e.value,
													),
											);

										return (
											<DataTableRow key={org.id}>
												<DataTableCell className="font-medium">
													{org.name}
												</DataTableCell>
												<DataTableCell>
													{!hasDataProvider ? (
														<ManualEntityIdInput
															orgId={org.id}
															value={org.formData.entity_id}
															onCommit={onUpdateOrgMapping}
														/>
													) : autoMatchSuggestions.has(
														org.id,
													) ? (
														<MatchSuggestionBadge
															suggestion={
																autoMatchSuggestions.get(
																	org.id,
																)!
															}
															onAccept={() =>
																onAcceptSuggestion(
																	org.id,
																)
															}
															onReject={() =>
																onRejectSuggestion(
																	org.id,
																)
															}
														/>
													) : (
														<EntitySelector
															entities={
																availableEntities
															}
															value={
																org
																	.formData
																	.entity_id
															}
															onChange={(
																value,
																label,
															) =>
																onUpdateOrgMapping(
																	org.id,
																	value,
																	label,
																)
															}
															isLoading={
																isLoadingEntities
															}
															isError={isEntitiesError}
															placeholder="Select entity..."
														/>
													)}
												</DataTableCell>
												<DataTableCell>
													{org.mapping ? (
														<Badge
															variant="default"
															className="bg-green-600"
														>
															<CheckCircle2 className="h-3 w-3 mr-1" />
															Mapped
														</Badge>
													) : org.formData
															.entity_id ? (
														<Badge variant="secondary">
															<Plus className="h-3 w-3 mr-1" />
															New
														</Badge>
													) : (
														<Badge variant="outline">
															Not Mapped
														</Badge>
													)}
												</DataTableCell>
												<DataTableCell>
													{!hasOAuth ? (
														<span className="text-xs text-muted-foreground">—</span>
													) : org.mapping?.connection_status === "completed" ? (
														<ConnectedBadge
															expiresAt={org.mapping?.connection_expires_at ?? null}
															isRefreshing={
																refreshingMappingId === org.mapping?.id
															}
															onRefresh={() =>
																onRefreshMapping(org.mapping!.id)
															}
														/>
													) : org.mapping?.connection_status === "failed" ? (
														<Badge variant="destructive" title={org.mapping?.connection_message ?? ""}>
															Failed
														</Badge>
													) : org.mapping?.connection_status === "expired" ? (
														<Badge className="bg-yellow-600">Expired</Badge>
													) : (
														<Button
															size="sm"
															variant="outline"
															onClick={() => onConnectMapping(org)}
														>
															Connect
														</Button>
													)}
												</DataTableCell>
												<DataTableCell className="text-right">
													<div className="flex gap-1 justify-end">
														<Button
															size="sm"
															variant="ghost"
															onClick={() =>
																onOpenConfigDialog(
																	org.id,
																)
															}
															title="Configure"
															className="relative"
														>
															<Settings className="h-4 w-4" />
															{hasNonDefaultConfig(
																org,
															) && (
																<span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-blue-600" />
															)}
														</Button>
														{org.mapping?.oauth_token_id && (
															<Button
																size="sm"
																variant="ghost"
																onClick={() =>
																	onDisconnectMapping(
																		org.mapping!.id,
																	)
																}
																title="Disconnect OAuth"
															>
																<PlugZap className="h-4 w-4" />
															</Button>
														)}
														<Button
															size="sm"
															variant="ghost"
															onClick={() =>
																onDeleteMapping(
																	org,
																)
															}
															disabled={
																!org.mapping ||
																isDeletePending
															}
															title={
																org.mapping
																	? "Unlink mapping"
																	: "No mapping to unlink"
															}
															className="text-red-600 hover:text-red-700 disabled:text-muted-foreground"
														>
															<Unlink className="h-4 w-4" />
														</Button>
													</div>
												</DataTableCell>
											</DataTableRow>
										);
									})}
								</DataTableBody>
							</DataTable>
						</div>
					</>
				)}
			</CardContent>
		</Card>
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

function ConnectedBadge({
	expiresAt,
	isRefreshing,
	onRefresh,
}: {
	expiresAt: string | null;
	isRefreshing: boolean;
	onRefresh: () => void;
}) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Badge className="bg-green-600 inline-flex items-center gap-1 pr-1">
					<span>Connected</span>
					<button
						type="button"
						onClick={(e) => {
							e.stopPropagation();
							if (!isRefreshing) onRefresh();
						}}
						disabled={isRefreshing}
						className="rounded p-0.5 hover:bg-green-700 focus:outline-none focus-visible:ring-1 focus-visible:ring-white disabled:cursor-default"
						title={isRefreshing ? "Refreshing…" : "Refresh token"}
						aria-label="Refresh token"
					>
						<RefreshCw
							className={`h-3 w-3 ${isRefreshing ? "animate-spin" : ""}`}
						/>
					</button>
				</Badge>
			</TooltipTrigger>
			<TooltipContent>
				{isRefreshing ? "Refreshing…" : formatTimeUntil(expiresAt)}
			</TooltipContent>
		</Tooltip>
	);
}

function ManualEntityIdInput({
	orgId,
	value,
	onCommit,
}: {
	orgId: string;
	value: string;
	onCommit: (orgId: string, entityId: string, entityName: string) => void;
}) {
	const [local, setLocal] = useState(value);

	return (
		<Input
			value={local}
			onChange={(e) => setLocal(e.target.value)}
			onBlur={() => {
				if (local !== value) {
					onCommit(orgId, local, local);
				}
			}}
			placeholder="Entity ID"
		/>
	);
}
