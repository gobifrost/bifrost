/**
 * MCP Servers — list view (mockup §2).
 *
 * Server templates are global / cross-org definitions of remote MCP services.
 * They carry auth shape (URLs, scopes, audience) but no client_id/secret —
 * those live on per-org connections.
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { $api } from "@/lib/api-client";
import { MCPServerForm } from "@/components/mcp/MCPServerForm";

export function MCPServers() {
	const navigate = useNavigate();
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [searchTerm, setSearchTerm] = useState("");

	// Server summary list (no nested connections — that's per-detail)
	const {
		data: servers = [],
		isLoading,
		refetch,
	} = $api.useQuery("get", "/api/mcp-servers", {
		params: { query: { active_only: false } },
	});

	// Pull all connections to compute per-server connection counts in one shot.
	// The API doesn't return aggregates on the summary endpoint.
	const { data: connections = [] } = $api.useQuery(
		"get",
		"/api/mcp-connections",
		{ params: { query: {} } },
	);

	const connectionsByServer = useMemo(() => {
		const map = new Map<string, number>();
		for (const c of connections) {
			map.set(c.server_id, (map.get(c.server_id) ?? 0) + 1);
		}
		return map;
	}, [connections]);

	const filtered = useSearch(servers, searchTerm, ["name", "server_url"]);

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						MCP Servers
					</h1>
					<p className="mt-2 text-muted-foreground">
						Templates for remote Model Context Protocol services.
						Per-org credentials live on connections.
					</p>
				</div>
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled
						title="Coming soon — manifest import"
					>
						<Upload className="h-4 w-4 mr-1" />
						Import from manifest
					</Button>
					<Button
						variant="default"
						size="sm"
						onClick={() => setIsCreateOpen(true)}
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
					className="flex-1"
				/>
			</div>

			{/* Content */}
			{isLoading ? (
				<div className="space-y-2">
					{[...Array(3)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : filtered.length > 0 ? (
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
									connectionsByServer.get(server.id) ?? 0;
								return (
									<DataTableRow
										key={server.id}
										clickable
										onClick={() =>
											navigate(
												`/mcp-servers/${server.id}`,
											)
										}
									>
										<DataTableCell className="font-medium">
											{server.name}
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
											{connCount === 0
												? "0 orgs"
												: connCount === 1
													? "1 org"
													: `${connCount} orgs`}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											<DiscoveryBadge serverId={server.id} />
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											{server.is_active ? (
												<Badge
													variant="default"
													className="bg-green-600 hover:bg-green-700"
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
								className="mt-4"
							>
								<Plus className="h-4 w-4 mr-1" />
								New Server
							</Button>
						)}
					</CardContent>
				</Card>
			)}

			<Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
				<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
					<DialogHeader>
						<DialogTitle>New MCP Server</DialogTitle>
						<DialogDescription>
							Discovery-first: paste the MCP endpoint, then run
							discovery to populate OAuth fields.
						</DialogDescription>
					</DialogHeader>
					<MCPServerForm
						onCancel={() => setIsCreateOpen(false)}
						onSuccess={(serverId) => {
							setIsCreateOpen(false);
							navigate(`/mcp-servers/${serverId}`);
						}}
					/>
				</DialogContent>
			</Dialog>
		</div>
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
	const { data: server } = $api.useQuery(
		"get",
		"/api/mcp-servers/{server_id}",
		{ params: { path: { server_id: serverId } } },
	);

	if (!server) {
		return <Badge variant="secondary">…</Badge>;
	}

	const meta = server.discovery_metadata as
		| { _source?: string }
		| null
		| undefined;
	const isManual = meta?._source === "manual";

	if (!server.discovery_metadata) {
		return <Badge variant="secondary">None</Badge>;
	}
	return isManual ? (
		<Badge
			variant="default"
			className="bg-amber-600 hover:bg-amber-700"
		>
			Manual
		</Badge>
	) : (
		<Badge variant="default" className="bg-green-600 hover:bg-green-700">
			Auto
		</Badge>
	);
}
