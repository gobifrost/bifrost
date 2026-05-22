import { useMemo, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import {
	ChevronLeft,
	Pencil,
	Trash2,
	Users,
	FileText,
	Bot,
	LayoutGrid,
	Workflow,
	BookOpen,
	Plus,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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
import { SearchBox } from "@/components/search/SearchBox";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import {
	useRoles,
	useDeleteRole,
	useRoleUsers,
	useRoleForms,
	useRoleAgents,
	useRoleApps,
	useRoleWorkflows,
	useRoleKnowledge,
	useAssignUsersToRole,
	useAssignFormsToRole,
	useAssignAgentsToRole,
	useAssignAppsToRole,
	useAssignWorkflowsToRole,
	useAssignKnowledgeToRole,
	useBulkUnassignUsers,
	useBulkUnassignForms,
	useBulkUnassignAgents,
	useBulkUnassignApps,
	useBulkUnassignWorkflows,
	useBulkUnassignKnowledge,
} from "@/hooks/useRoles";
import { useUsersFiltered } from "@/hooks/useUsers";
import { useForms } from "@/hooks/useForms";
import { useAgents } from "@/hooks/useAgents";
import { useApplications } from "@/hooks/useApplications";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useOrganizations } from "@/hooks/useOrganizations";
import { RoleDialog } from "@/components/roles/RoleDialog";
import {
	ConsumerTab,
	type ConsumerTabItem,
} from "@/components/roles/ConsumerTab";

import type { components } from "@/lib/v1";

type ConsumerKey =
	| "users"
	| "forms"
	| "agents"
	| "apps"
	| "workflows"
	| "knowledge";

const TABS: { key: ConsumerKey; label: string; Icon: React.ComponentType<{ className?: string }> }[] = [
	{ key: "users", label: "Users", Icon: Users },
	{ key: "forms", label: "Forms", Icon: FileText },
	{ key: "agents", label: "Agents", Icon: Bot },
	{ key: "apps", label: "Apps", Icon: LayoutGrid },
	{ key: "workflows", label: "Workflows", Icon: Workflow },
	{ key: "knowledge", label: "Knowledge", Icon: BookOpen },
];

export function RoleDetail() {
	const { roleId, tab } = useParams<{ roleId: string; tab?: string }>();
	const navigate = useNavigate();

	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	const { data: roles, isLoading: rolesLoading } = useRoles();
	const role = useMemo(
		() => roles?.find((r) => r.id === roleId),
		[roles, roleId],
	);
	const deleteRole = useDeleteRole();

	const currentTab: ConsumerKey =
		tab && TABS.some((t) => t.key === tab) ? (tab as ConsumerKey) : "users";

	if (!roleId) {
		return (
			<div className="p-8 text-center text-muted-foreground">
				Missing role id.
			</div>
		);
	}

	if (rolesLoading) {
		return (
			<div className="space-y-4 max-w-7xl mx-auto">
				<Skeleton className="h-8 w-64" />
				<Skeleton className="h-16 w-full" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (!role) {
		return (
			<div className="p-8 text-center">
				<p className="text-muted-foreground mb-4">
					Role not found. It may have been deleted.
				</p>
				<Button variant="outline" asChild>
					<Link to="/roles">
						<ChevronLeft className="h-4 w-4 mr-1" />
						Back to roles
					</Link>
				</Button>
			</div>
		);
	}

	const handleDelete = () => {
		deleteRole.mutate(
			{ params: { path: { role_id: role.id } } },
			{
				onSuccess: () => {
					navigate("/roles");
				},
			},
		);
		setDeleteOpen(false);
	};

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Breadcrumb */}
			<div className="text-sm">
				<Link
					to="/roles"
					className="text-muted-foreground hover:text-foreground inline-flex items-center"
				>
					<ChevronLeft className="h-4 w-4 mr-1" />
					Roles
				</Link>
				<span className="text-muted-foreground mx-2">/</span>
				<span className="font-medium">{role.name}</span>
			</div>

			{/* Header */}
			<div className="flex items-start justify-between gap-4">
				<div className="min-w-0 flex-1">
					<h1 className="text-3xl font-extrabold tracking-tight">
						{role.name}
					</h1>
					{role.description && (
						<p className="mt-1 text-muted-foreground">{role.description}</p>
					)}
					<p className="mt-2 text-xs text-muted-foreground">
						A role grants access to every user, form, agent, app, workflow, and
						knowledge namespace you assign below.
					</p>
				</div>
				<div className="flex gap-2">
					<Button variant="outline" onClick={() => setEditOpen(true)}>
						<Pencil className="h-4 w-4 mr-1.5" />
						Edit
					</Button>
					<Button
						variant="outline"
						className="text-destructive hover:text-destructive"
						onClick={() => setDeleteOpen(true)}
					>
						<Trash2 className="h-4 w-4 mr-1.5" />
						Delete
					</Button>
				</div>
			</div>

			{/* Tabs */}
			<Tabs
				value={currentTab}
				onValueChange={(v) => navigate(`/roles/${role.id}/${v}`)}
				className="flex-1 min-h-0 flex flex-col"
			>
				<TabsList className="self-start">
					{TABS.map(({ key, label, Icon }) => {
						const count = role.consumer_counts?.[key] ?? 0;
						return (
							<TabsTrigger key={key} value={key} className="gap-1.5">
								<Icon className="h-4 w-4" />
								{label}
								<span className="ml-1 text-xs text-muted-foreground">
									{count}
								</span>
							</TabsTrigger>
						);
					})}
				</TabsList>

				<TabsContent value="users" className="flex-1 min-h-0">
					<UsersTab roleId={role.id} />
				</TabsContent>
				<TabsContent value="forms" className="flex-1 min-h-0">
					<FormsTab roleId={role.id} />
				</TabsContent>
				<TabsContent value="agents" className="flex-1 min-h-0">
					<AgentsTab roleId={role.id} />
				</TabsContent>
				<TabsContent value="apps" className="flex-1 min-h-0">
					<AppsTab roleId={role.id} />
				</TabsContent>
				<TabsContent value="workflows" className="flex-1 min-h-0">
					<WorkflowsTab roleId={role.id} />
				</TabsContent>
				<TabsContent value="knowledge" className="flex-1 min-h-0">
					<KnowledgeTab roleId={role.id} />
				</TabsContent>
			</Tabs>

			<RoleDialog
				role={role}
				open={editOpen}
				onClose={() => setEditOpen(false)}
			/>

			<AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete role</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete "{role.name}"? This action cannot
							be undone and removes the role from every user, form, and other
							consumer it's assigned to.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete role
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

// =============================================================================
// Tab implementations
// =============================================================================

/**
 * Look up an organization by id and return the {id, name, isProvider} triple
 * the ConsumerTab OrgBadge expects. Shared by every tab so the lookup
 * happens once per role-detail render.
 */
function useOrgLookup() {
	const { data: orgs } = useOrganizations();
	return useMemo(() => {
		const byId = new Map<string, components["schemas"]["OrganizationPublic"]>();
		for (const o of orgs ?? []) byId.set(o.id, o);
		return (orgId: string | null | undefined) => {
			if (!orgId) return { id: null, name: "Platform", isProvider: false };
			const o = byId.get(orgId);
			return {
				id: orgId,
				name: o?.name ?? orgId,
				isProvider: o?.is_provider ?? false,
			};
		};
	}, [orgs]);
}

function UsersTab({ roleId }: { roleId: string }) {
	const { data: assigned, isLoading } = useRoleUsers(roleId);
	const { data: allUsers, isLoading: loadingAll } = useUsersFiltered();
	const assignMut = useAssignUsersToRole();
	const unassignMut = useBulkUnassignUsers();
	const orgFor = useOrgLookup();

	const assignedIds = useMemo(
		() => new Set(assigned?.user_ids ?? []),
		[assigned],
	);
	const userById = useMemo(() => {
		const m = new Map<string, components["schemas"]["UserPublic"]>();
		for (const u of allUsers ?? []) m.set(u.id, u);
		return m;
	}, [allUsers]);

	const items: ConsumerTabItem[] = useMemo(
		() =>
			Array.from(assignedIds).map((id) => {
				const u = userById.get(id);
				return {
					id,
					primary: u?.name || u?.email || id,
					secondary: u?.email && u?.name !== u?.email ? u.email : null,
					org: orgFor(u?.organization_id),
				};
			}),
		[assignedIds, userById, orgFor],
	);

	const candidates: ConsumerTabItem[] = useMemo(
		() =>
			(allUsers ?? []).map((u) => ({
				id: u.id,
				primary: u.name || u.email,
				secondary: u.email && u.name !== u.email ? u.email : null,
				org: orgFor(u.organization_id),
			})),
		[allUsers, orgFor],
	);

	return (
		<ConsumerTab
			items={items}
			isLoading={isLoading}
			candidates={candidates}
			candidatesLoading={loadingAll}
			consumerLabel="users"
			emptyHint="No users assigned to this role yet."
			primaryColumnLabel="Name"
			secondaryColumnLabel="Email"
			showOrgColumn
			onAssign={async (ids) => {
				await assignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { user_ids: ids },
				});
			}}
			onUnassign={async (ids) => {
				await unassignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { user_ids: ids },
				});
			}}
		/>
	);
}

function FormsTab({ roleId }: { roleId: string }) {
	const { data: assigned, isLoading } = useRoleForms(roleId);
	const { data: allForms, isLoading: loadingAll } = useForms();
	const assignMut = useAssignFormsToRole();
	const unassignMut = useBulkUnassignForms();
	const orgFor = useOrgLookup();

	const formById = useMemo(() => {
		const m = new Map<string, components["schemas"]["FormPublic"]>();
		for (const f of allForms ?? []) m.set(f.id, f);
		return m;
	}, [allForms]);

	const items: ConsumerTabItem[] = useMemo(
		() =>
			(assigned?.form_ids ?? []).map((id) => {
				const f = formById.get(id);
				return {
					id,
					primary: f?.name || id,
					secondary: f?.description || null,
					org: orgFor(f?.organization_id),
				};
			}),
		[assigned, formById, orgFor],
	);

	const candidates: ConsumerTabItem[] = useMemo(
		() =>
			(allForms ?? []).map((f) => ({
				id: f.id,
				primary: f.name,
				secondary: f.description || null,
				org: orgFor(f.organization_id),
			})),
		[allForms, orgFor],
	);

	return (
		<ConsumerTab
			items={items}
			isLoading={isLoading}
			candidates={candidates}
			candidatesLoading={loadingAll}
			consumerLabel="forms"
			emptyHint="No forms assigned to this role yet."
			showOrgColumn
			onAssign={async (ids) => {
				await assignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { form_ids: ids },
				});
			}}
			onUnassign={async (ids) => {
				await unassignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { form_ids: ids },
				});
			}}
		/>
	);
}

function AgentsTab({ roleId }: { roleId: string }) {
	const { data: assigned, isLoading } = useRoleAgents(roleId);
	const { data: allAgents, isLoading: loadingAll } = useAgents();
	const assignMut = useAssignAgentsToRole();
	const unassignMut = useBulkUnassignAgents();
	const orgFor = useOrgLookup();

	type AgentLite = {
		id: string;
		name: string;
		description?: string | null;
		organization_id?: string | null;
	};

	const agentById = useMemo(() => {
		const m = new Map<string, AgentLite>();
		for (const a of (allAgents ?? []) as AgentLite[]) m.set(a.id, a);
		return m;
	}, [allAgents]);

	const items: ConsumerTabItem[] = useMemo(
		() =>
			(assigned?.agent_ids ?? []).map((id) => {
				const a = agentById.get(id);
				return {
					id,
					primary: a?.name || id,
					secondary: a?.description || null,
					org: orgFor(a?.organization_id),
				};
			}),
		[assigned, agentById, orgFor],
	);

	const candidates: ConsumerTabItem[] = useMemo(
		() =>
			((allAgents ?? []) as AgentLite[]).map((a) => ({
				id: a.id,
				primary: a.name,
				secondary: a.description || null,
				org: orgFor(a.organization_id),
			})),
		[allAgents, orgFor],
	);

	return (
		<ConsumerTab
			items={items}
			isLoading={isLoading}
			candidates={candidates}
			candidatesLoading={loadingAll}
			consumerLabel="agents"
			emptyHint="No agents assigned to this role yet."
			showOrgColumn
			onAssign={async (ids) => {
				await assignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { agent_ids: ids },
				});
			}}
			onUnassign={async (ids) => {
				await unassignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { agent_ids: ids },
				});
			}}
		/>
	);
}

function AppsTab({ roleId }: { roleId: string }) {
	const { data: assigned, isLoading } = useRoleApps(roleId);
	const { data: allAppsResp, isLoading: loadingAll } = useApplications();
	const assignMut = useAssignAppsToRole();
	const unassignMut = useBulkUnassignApps();
	const orgFor = useOrgLookup();

	const allApps = useMemo(() => {
		if (Array.isArray(allAppsResp)) return allAppsResp;
		return (allAppsResp as { applications?: components["schemas"]["ApplicationPublic"][] } | undefined)?.applications ?? [];
	}, [allAppsResp]);

	const appById = useMemo(() => {
		const m = new Map<string, components["schemas"]["ApplicationPublic"]>();
		for (const a of allApps) m.set(a.id, a);
		return m;
	}, [allApps]);

	const items: ConsumerTabItem[] = useMemo(
		() =>
			(assigned?.app_ids ?? []).map((id) => {
				const a = appById.get(id);
				return {
					id,
					primary: a?.name || id,
					secondary: a?.description || null,
					org: orgFor(a?.organization_id),
				};
			}),
		[assigned, appById, orgFor],
	);

	const candidates: ConsumerTabItem[] = useMemo(
		() =>
			allApps.map((a) => ({
				id: a.id,
				primary: a.name,
				secondary: a.description || null,
				org: orgFor(a.organization_id),
			})),
		[allApps, orgFor],
	);

	return (
		<ConsumerTab
			items={items}
			isLoading={isLoading}
			candidates={candidates}
			candidatesLoading={loadingAll}
			consumerLabel="apps"
			emptyHint="No apps assigned to this role yet."
			showOrgColumn
			onAssign={async (ids) => {
				await assignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { app_ids: ids },
				});
			}}
			onUnassign={async (ids) => {
				await unassignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { app_ids: ids },
				});
			}}
		/>
	);
}

function WorkflowsTab({ roleId }: { roleId: string }) {
	const { data: assigned, isLoading } = useRoleWorkflows(roleId);
	const { data: allWorkflows, isLoading: loadingAll } = useWorkflows();
	const assignMut = useAssignWorkflowsToRole();
	const unassignMut = useBulkUnassignWorkflows();
	const orgFor = useOrgLookup();

	type WorkflowLite = {
		id: string;
		name?: string;
		description?: string | null;
		organization_id?: string | null;
	};

	const workflowById = useMemo(() => {
		const m = new Map<string, WorkflowLite>();
		for (const w of (allWorkflows ?? []) as WorkflowLite[]) m.set(w.id, w);
		return m;
	}, [allWorkflows]);

	const items: ConsumerTabItem[] = useMemo(
		() =>
			(assigned?.workflow_ids ?? []).map((id) => {
				const w = workflowById.get(id);
				return {
					id,
					primary: w?.name || id,
					secondary: w?.description || null,
					org: orgFor(w?.organization_id),
				};
			}),
		[assigned, workflowById, orgFor],
	);

	const candidates: ConsumerTabItem[] = useMemo(
		() =>
			((allWorkflows ?? []) as WorkflowLite[]).map((w) => ({
				id: w.id,
				primary: w.name || w.id,
				secondary: w.description || null,
				org: orgFor(w.organization_id),
			})),
		[allWorkflows, orgFor],
	);

	return (
		<ConsumerTab
			items={items}
			isLoading={isLoading}
			candidates={candidates}
			candidatesLoading={loadingAll}
			consumerLabel="workflows"
			emptyHint="No workflows assigned to this role yet."
			showOrgColumn
			onAssign={async (ids) => {
				await assignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { workflow_ids: ids },
				});
			}}
			onUnassign={async (ids) => {
				await unassignMut.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { workflow_ids: ids },
				});
			}}
		/>
	);
}

// =============================================================================
// KnowledgeTab — special, since assignments are namespace+org pairs not entity-ids
// =============================================================================

function KnowledgeTab({ roleId }: { roleId: string }) {
	const { data, isLoading } = useRoleKnowledge(roleId);
	const { data: orgs } = useOrganizations();
	const assignMut = useAssignKnowledgeToRole();
	const unassignMut = useBulkUnassignKnowledge();

	const [search, setSearch] = useState("");
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [drawerOpen, setDrawerOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);

	const entries = useMemo(() => data?.entries ?? [], [data]);

	const orgName = useMemo(
		() =>
			(orgId?: string | null) => {
				if (!orgId) return "All organizations";
				const o = orgs?.find((x) => x.id === orgId);
				return o?.name || orgId;
			},
		[orgs],
	);

	const visible = useMemo(() => {
		const q = search.trim().toLowerCase();
		if (!q) return entries;
		return entries.filter((e) =>
			e.namespace.toLowerCase().includes(q) ||
			orgName(e.organization_id).toLowerCase().includes(q),
		);
	}, [entries, search, orgName]);

	const visibleIds = useMemo(() => new Set(visible.map((e) => e.id)), [visible]);
	const effective = useMemo(() => {
		const out = new Set<string>();
		for (const id of selected) if (visibleIds.has(id)) out.add(id);
		return out;
	}, [selected, visibleIds]);

	const allSelected =
		visible.length > 0 && visible.every((e) => effective.has(e.id));

	const toggle = (id: string) =>
		setSelected((prev) => {
			const next = new Set<string>();
			for (const s of prev) if (visibleIds.has(s)) next.add(s);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const toggleAll = () =>
		setSelected((prev) => {
			const next = new Set<string>();
			for (const s of prev) if (visibleIds.has(s)) next.add(s);
			if (allSelected) {
				for (const e of visible) next.delete(e.id);
			} else {
				for (const e of visible) next.add(e.id);
			}
			return next;
		});

	const handleUnassign = async () => {
		const ids = Array.from(effective);
		if (ids.length === 0) return;
		setSubmitting(true);
		try {
			await unassignMut.mutateAsync({
				params: { path: { role_id: roleId } },
				body: { assignment_ids: ids },
			});
			toast.success(`Removed ${ids.length} knowledge assignment(s)`);
			setSelected(new Set());
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to remove knowledge",
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<div className="flex flex-col gap-3">
			<div className="flex items-center gap-3">
				<SearchBox
					value={search}
					onChange={setSearch}
					placeholder="Search namespaces..."
					className="flex-1"
				/>
				<Button onClick={() => setDrawerOpen(true)}>
					<Plus className="h-4 w-4 mr-1.5" />
					Assign namespace
				</Button>
			</div>

			{isLoading ? (
				<div className="space-y-2">
					{[...Array(3)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : entries.length === 0 ? (
				<div className="text-sm text-muted-foreground py-8 text-center border rounded">
					No knowledge namespaces assigned to this role yet.
				</div>
			) : (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead className="w-0 whitespace-nowrap">
								<Checkbox
									checked={
										allSelected
											? true
											: effective.size > 0
												? "indeterminate"
												: false
									}
									onCheckedChange={toggleAll}
									aria-label="Select all visible namespaces"
								/>
							</DataTableHead>
							<DataTableHead>Namespace</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Scope
							</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{visible.map((e) => (
							<DataTableRow key={e.id}>
								<DataTableCell className="w-0 whitespace-nowrap">
									<Checkbox
										checked={effective.has(e.id)}
										onCheckedChange={() => toggle(e.id)}
										aria-label={`Select ${e.namespace}`}
									/>
								</DataTableCell>
								<DataTableCell className="font-medium">
									{e.namespace}
								</DataTableCell>
								<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
									{orgName(e.organization_id)}
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			)}

			{effective.size > 0 && (
				<div className="sticky bottom-2 flex items-center gap-3 rounded-lg border bg-popover px-4 py-2 shadow-lg">
					<span className="text-sm font-medium">
						{effective.size} selected
					</span>
					<Button
						variant="destructive"
						size="sm"
						disabled={submitting}
						onClick={handleUnassign}
					>
						{submitting ? "Unassigning..." : "Unassign from role"}
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="ml-auto"
						onClick={() => setSelected(new Set())}
					>
						Clear
					</Button>
				</div>
			)}

			{drawerOpen && (
				<KnowledgeAssignDrawer
					roleId={roleId}
					onClose={() => setDrawerOpen(false)}
					onAssign={async (entries) => {
						await assignMut.mutateAsync({
							params: { path: { role_id: roleId } },
							body: { entries },
						});
					}}
				/>
			)}
		</div>
	);
}

// Small dedicated drawer for knowledge — accepts free-text namespace + org choice
// since we can't enumerate "all possible namespaces" the way we can for entities.

function KnowledgeAssignDrawer({
	onClose,
	onAssign,
}: {
	roleId: string;
	onClose: () => void;
	onAssign: (
		entries: { namespace: string; organization_id?: string | null }[],
	) => Promise<void>;
}) {
	const { data: orgs } = useOrganizations();
	const [namespace, setNamespace] = useState("");
	const [orgId, setOrgId] = useState<string>("global");
	const [submitting, setSubmitting] = useState(false);

	const handleSubmit = async () => {
		const ns = namespace.trim();
		if (!ns) {
			toast.error("Namespace is required");
			return;
		}
		setSubmitting(true);
		try {
			await onAssign([
				{
					namespace: ns,
					organization_id: orgId === "global" ? null : orgId,
				},
			]);
			toast.success(`Assigned namespace "${ns}"`);
			setNamespace("");
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Failed to assign namespace",
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Sheet open onOpenChange={(o) => !o && onClose()}>
			<SheetContent side="right" className="w-[480px] sm:max-w-[480px] flex flex-col">
				<SheetHeader>
					<SheetTitle>Assign knowledge namespace</SheetTitle>
					<SheetDescription>
						Grant this role access to a knowledge namespace. Namespaces are
						free-form strings — pick the one used in your knowledge store.
					</SheetDescription>
				</SheetHeader>

				<div className="px-4 space-y-3">
					<label className="block text-sm">
						<span className="block mb-1 text-muted-foreground">Namespace</span>
						<input
							type="text"
							value={namespace}
							onChange={(e) => setNamespace(e.target.value)}
							placeholder="e.g. customer-docs"
							className="w-full border rounded px-3 py-2 text-sm bg-background"
						/>
					</label>

					<label className="block text-sm">
						<span className="block mb-1 text-muted-foreground">Scope</span>
						<select
							value={orgId}
							onChange={(e) => setOrgId(e.target.value)}
							className="w-full border rounded px-3 py-2 text-sm bg-background"
						>
							<option value="global">All organizations</option>
							{(orgs ?? []).map((o) => (
								<option key={o.id} value={o.id}>
									{o.name}
								</option>
							))}
						</select>
					</label>
				</div>

				<SheetFooter>
					<Button variant="outline" onClick={onClose}>
						Close
					</Button>
					<Button disabled={submitting} onClick={handleSubmit}>
						{submitting ? "Assigning..." : "Assign"}
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}
