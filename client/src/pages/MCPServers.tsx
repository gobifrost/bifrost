import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import type { components } from "@/lib/v1";
/**
 * MCP Servers — list view (mockup §2).
 *
 * Server templates are global / cross-org definitions of remote MCP services.
 * They carry auth shape (URLs, scopes, audience) but no client_id/secret —
 * those live on per-org connections.
 */

import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Plus, RefreshCw, ServerCog, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { $api } from "@/lib/api-client";
import { MCPServerForm } from "@/components/mcp/MCPServerForm";

export function MCPServers() {
	const navigate = useNavigate();
	const compact = useMediaQuery("(max-width: 1023px)");
	const returnFocus = useDialogReturnFocus();
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const createPending = useRef(false);
	const [searchTerm, setSearchTerm] = useState("");

	// Server summary list (no nested connections — that's per-detail)
	const {
		data: servers = [],
		isLoading,
		isError,
		isFetching,
		refetch,
	} = $api.useQuery("get", "/api/mcp-servers", {
		params: { query: { active_only: false } },
	});

	// Pull all connections to compute per-server connection counts in one shot.
	// The API doesn't return aggregates on the summary endpoint.
	const {
		data: connections = [],
		isError: connectionsError,
		isLoading: connectionsLoading,
		isFetching: connectionsFetching,
		refetch: retryConnections,
	} = $api.useQuery("get", "/api/mcp-connections", { params: { query: {} } });

	const connectionsByServer = useMemo(() => {
		const map = new Map<string, number>();
		for (const c of connections) {
			map.set(c.server_id, (map.get(c.server_id) ?? 0) + 1);
		}
		return map;
	}, [connections]);

	const filtered = useSearch(servers, searchTerm, ["name", "server_url"]);

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1400px] gap-5">
			{/* Header */}
			<div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
				<div>
					<h1 className="font-display text-2xl font-semibold sm:text-3xl">
						MCP Servers
					</h1>
					<p className="mt-2 text-muted-foreground">
						Templates for remote Model Context Protocol services.
						Per-org credentials live on connections.
					</p>
				</div>
				<div className="grid w-full grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] gap-2 xl:w-auto xl:flex xl:flex-wrap xl:justify-end [&>button]:min-h-11">
					<Button
						variant="outline"
						size="icon"
						onClick={() => {
							void refetch();
							void retryConnections();
						}}
						aria-label="Refresh MCP servers"
						disabled={isFetching || connectionsFetching}
						className="size-11 shrink-0"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled
						title="Coming soon — manifest import"
						className="min-w-0"
					>
						<Upload className="h-4 w-4 mr-1" />
						Import from manifest
					</Button>
					<Button
						variant="default"
						size="sm"
						onClick={() => setIsCreateOpen(true)}
						className="min-w-0"
					>
						<Plus className="h-4 w-4 mr-1" />
						New Server
					</Button>
				</div>
			</div>

			{/* Search */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name or URL..."
					className="min-w-0 flex-1 [&_input]:h-11"
				/>
			</div>

			{isError && (
				<div
					role="alert"
					className="rounded-[var(--bf-radius-surface)] border p-4 text-sm"
				>
					MCP servers could not load. Use Refresh MCP servers to try
					again.
				</div>
			)}
			{connectionsError && (
				<div
					role="alert"
					className="rounded-[var(--bf-radius-surface)] border p-4 text-sm"
				>
					Connection counts could not load. Use Refresh MCP servers to
					try again.
				</div>
			)}
			{/* Content */}
			<PageScrollArea
				aria-label="MCP servers list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
			>
				{isLoading ? (
					<div
						role="status"
						aria-label="Loading MCP servers"
						className="space-y-2"
					>
						{[...Array(3)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : isError && servers.length === 0 ? null : filtered.length >
				  0 ? (
					compact ? (
						<MCPServerCards
							servers={filtered}
							counts={connectionsByServer}
							countsUnavailable={
								connectionsError || connectionsLoading
							}
						/>
					) : (
						<div className="flex-1 min-h-0">
							<DataTable className="max-h-full">
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead>Name</DataTableHead>
										<DataTableHead>URL</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Connections
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Discovery
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Status
										</DataTableHead>
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{filtered.map((server) => {
										const connCount =
											connectionsByServer.get(
												server.id,
											) ?? 0;
										return (
											<DataTableRow
												key={server.id}
												clickable
												href={`/mcp-servers/${server.id}`}
												onClick={() =>
													navigate(
														`/mcp-servers/${server.id}`,
													)
												}
											>
												<DataTableCell className="font-medium">
													<Link
														to={`/mcp-servers/${server.id}`}
														onClick={(event) =>
															event.stopPropagation()
														}
														className="flex min-h-11 items-center rounded-[var(--bf-radius-control)] [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
													>
														{server.name}
													</Link>
													{server.organization_id ? (
														<div className="text-xs text-muted-foreground">
															Org-scoped
														</div>
													) : (
														<div className="text-xs text-muted-foreground">
															Platform template
														</div>
													)}
												</DataTableCell>
												<DataTableCell>
													<code className="text-xs break-all">
														{server.server_url}
													</code>
												</DataTableCell>
												<DataTableCell className="w-0 whitespace-nowrap">
													{connectionsError ||
													connectionsLoading
														? "Unavailable"
														: connCount === 0
															? "0 orgs"
															: connCount === 1
																? "1 org"
																: `${connCount} orgs`}
												</DataTableCell>
												<DataTableCell className="w-0 whitespace-nowrap">
													<DiscoveryBadge
														serverId={server.id}
													/>
												</DataTableCell>
												<DataTableCell className="w-0 whitespace-nowrap">
													{server.is_active ? (
														<Badge
															variant="default"
															className="bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
														>
															Active
														</Badge>
													) : (
														<Badge variant="secondary">
															Inactive
														</Badge>
													)}
												</DataTableCell>
											</DataTableRow>
										);
									})}
								</DataTableBody>
							</DataTable>
						</div>
					)
				) : (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12 text-center">
							<ServerCog className="h-12 w-12 text-muted-foreground" />
							<h3 className="mt-4 text-lg font-semibold">
								{searchTerm
									? "No MCP servers match your search"
									: "No MCP servers"}
							</h3>
							<p className="mt-2 text-sm text-muted-foreground max-w-md">
								{searchTerm
									? "Try adjusting your search term or clear the filter."
									: "Add an MCP server template to make remote tools available to agents."}
							</p>
							{!searchTerm && (
								<Button
									variant="outline"
									size="sm"
									onClick={() => setIsCreateOpen(true)}
									className="mt-4 min-h-11"
								>
									<Plus className="h-4 w-4 mr-1" />
									New Server
								</Button>
							)}
						</CardContent>
					</Card>
				)}
			</PageScrollArea>

			<Dialog
				open={isCreateOpen}
				onOpenChange={(open) => {
					if (!createPending.current) setIsCreateOpen(open);
				}}
			>
				<DialogContent
					{...returnFocus}
					className="flex max-w-2xl max-h-[90dvh] flex-col overflow-hidden"
					onEscapeKeyDown={(event) => {
						if (createPending.current) event.preventDefault();
					}}
					onPointerDownOutside={(event) => {
						if (createPending.current) event.preventDefault();
					}}
				>
					<DialogHeader className="shrink-0">
						<DialogTitle>New MCP Server</DialogTitle>
						<DialogDescription>
							Add a server URL, then discover its OAuth settings
							or enter them manually.
						</DialogDescription>
					</DialogHeader>
					<MCPServerForm
						onPendingChange={(pending) => {
							createPending.current = pending;
						}}
						onCancel={() => setIsCreateOpen(false)}
						onSuccess={(serverId) => {
							setIsCreateOpen(false);
							navigate(`/mcp-servers/${serverId}`);
						}}
					/>
				</DialogContent>
			</Dialog>
		</PageWorkspace>
	);
}

/**
 * Renders an Auto/Manual badge for the row, using the per-server detail
 * query so we get the discovery_metadata snapshot. Cheap because react-query
 * caches by key; multiple rows with the same server share the result.
 *
 * (We need to fetch detail rather than rely on summary — summary endpoint
 * doesn't include discovery_metadata to keep payloads small.)
 */
function DiscoveryBadge({ serverId }: { serverId: string }) {
	const { data: server, isError } = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: serverId } } },
	);

	if (isError && !server)
		return <Badge variant="secondary">Unavailable</Badge>;
	if (!server) {
		return <Badge variant="secondary">…</Badge>;
	}

	const meta = server.discovery_metadata as
		{ _source?: string } | null | undefined;
	const isManual = meta?._source === "manual";

	if (!server.discovery_metadata) {
		return <Badge variant="secondary">None</Badge>;
	}
	return isManual ? (
		<Badge
			variant="default"
			className="bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
		>
			Manual
		</Badge>
	) : (
		<Badge
			variant="default"
			className="bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
		>
			Auto
		</Badge>
	);
}

function MCPServerCards({
	servers,
	counts,
	countsUnavailable,
}: {
	servers: components["schemas"]["MCPServerSummary"][];
	counts: Map<string, number>;
	countsUnavailable: boolean;
}) {
	return (
		<ul aria-label="MCP servers" className="space-y-3">
			{servers.map((server) => (
				<li
					key={server.id}
					className="min-w-0 space-y-3 rounded-[var(--bf-radius-surface)] border bg-card p-4 [overflow-wrap:anywhere]"
				>
					<Link
						to={`/mcp-servers/${server.id}`}
						className="inline-flex min-h-11 max-w-full items-center font-medium text-primary [overflow-wrap:anywhere]"
					>
						{server.name}
					</Link>
					<p className="text-sm text-muted-foreground">
						{server.organization_id
							? "Organization template"
							: "Platform template"}
					</p>
					<p className="font-mono text-sm break-all">
						{server.server_url}
					</p>
					<dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
						<dt className="text-muted-foreground">Connections</dt>
						<dd>
							{countsUnavailable
								? "Unavailable"
								: (counts.get(server.id) ?? 0)}
						</dd>
						<dt className="text-muted-foreground">Discovery</dt>
						<dd>
							<DiscoveryBadge serverId={server.id} />
						</dd>
						<dt className="text-muted-foreground">Status</dt>
						<dd>
							<Badge
								variant="secondary"
								className={
									server.is_active
										? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
										: undefined
								}
							>
								{server.is_active ? "Active" : "Inactive"}
							</Badge>
						</dd>
					</dl>
				</li>
			))}
		</ul>
	);
}
