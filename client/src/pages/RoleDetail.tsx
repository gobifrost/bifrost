import { RoleDetailHeader } from "@/components/roles/RoleDetailHeader";
import { useMemo, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import {
	ChevronLeft,
	Users,
	FileText,
	Bot,
	LayoutGrid,
	ShieldCheck,
	Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { Skeleton } from "@/components/ui/skeleton";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { RoleDeleteDialog } from "@/components/roles/RoleDeleteDialog";

import {
	useRole,
	useDeleteRole,
	useRoleUsersPage,
	useRoleForms,
	useRoleAgents,
	useRoleApps,
	useRoleWorkflows,
	useAssignUsersToRole,
	useAssignFormsToRole,
	useAssignAgentsToRole,
	useAssignAppsToRole,
	useAssignWorkflowsToRole,
	useBulkUnassignUsers,
	useBulkUnassignForms,
	useBulkUnassignAgents,
	useBulkUnassignApps,
	useBulkUnassignWorkflows,
} from "@/hooks/useRoles";
import { useUsersFiltered } from "@/hooks/useUsers";
import { useForms } from "@/hooks/useForms";
import { useAgents } from "@/hooks/useAgents";
import { useApplications } from "@/hooks/useApplications";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useOrganizations } from "@/hooks/useOrganizations";
import { RoleDialog } from "@/components/roles/RoleDialog";
import { RolePeoplePanel } from "@/components/roles/RolePeoplePanel";
import { RolePermissionsPanel } from "@/components/roles/RolePermissionsPanel";
import { getErrorMessage } from "@/lib/api-error";
import { useAuthorization } from "@/services/authorization";
import {
	ConsumerTab,
	type ConsumerTabItem,
} from "@/components/roles/ConsumerTab";

import type { components } from "@/lib/v1";

type ConsumerKey = "users" | "forms" | "agents" | "apps" | "workflows";
type TabKey = ConsumerKey | "permissions";

const CONSUMER_TABS: {
	key: ConsumerKey;
	label: string;
	Icon: React.ComponentType<{ className?: string }>;
}[] = [
	{ key: "users", label: "Users", Icon: Users },
	{ key: "forms", label: "Forms", Icon: FileText },
	{ key: "agents", label: "Agents", Icon: Bot },
	{ key: "apps", label: "Apps", Icon: LayoutGrid },
	{ key: "workflows", label: "Workflows", Icon: Workflow },
];

export function RoleDetail() {
	const { roleId, tab } = useParams<{ roleId: string; tab?: string }>();
	const navigate = useNavigate();

	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	const {
		data: role,
		isLoading: rolesLoading,
		isError: roleError,
		isFetching: roleFetching,
		refetch: refetchRole,
	} = useRole(roleId);
	const deleteRole = useDeleteRole();
	const authorization = useAuthorization();

	if (!roleId) {
		return (
			<div className="p-8 text-center text-muted-foreground">
				Missing role id.
			</div>
		);
	}

	if (rolesLoading) {
		return (
			<div
				role="status"
				aria-label="Loading role"
				className="space-y-4 max-w-7xl mx-auto"
			>
				<Skeleton className="h-8 w-64" />
				<Skeleton className="h-16 w-full" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (roleError && !role)
		return (
			<div className="space-y-4">
				<h1 className="font-display text-2xl font-semibold">
					Role details
				</h1>
				<RoleReadError
					cached={false}
					pending={roleFetching}
					onRetry={() => {
						void refetchRole();
					}}
				/>
				<Button variant="outline" asChild className="min-h-11">
					<Link to="/roles">Back to roles</Link>
				</Button>
			</div>
		);
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

	// Built-in roles are shown read-only: their permissions, no consumers.
	const isBuiltin = role.is_builtin;
	const currentTab: TabKey =
		tab === "permissions"
			? "permissions"
			: CONSUMER_TABS.some((t) => t.key === tab)
				? (tab as ConsumerKey)
				: "users";
	const canManage =
		!isBuiltin &&
		authorization.meets({ permission: "roles.readwrite", at: "global" });
	// Built-in additional roles (Platform Operator, Secrets Reader) list
	// who holds them; base roles (Platform Admin, User) would list everyone.
	const showsPeople =
		isBuiltin &&
		!role.is_base &&
		authorization.meets({ permission: "roleassignments.read" });

	const handleDelete = () => {
		if (deleteRole.isPending) return;
		deleteRole.mutate(
			{ params: { path: { role_id: role.id } } },
			{
				onSuccess: () => {
					navigate("/roles");
				},
			},
		);
	};

	return (
		<PageWorkspace className="w-full max-w-7xl mx-auto gap-5">
			<RoleDetailHeader
				name={role.name}
				description={role.description}
				isBuiltin={isBuiltin}
				canManage={canManage}
				onEdit={() => setEditOpen(true)}
				onDelete={() => {
					deleteRole.reset();
					setDeleteOpen(true);
				}}
			/>

			{roleError && (
				<RoleReadError
					cached
					pending={roleFetching}
					onRetry={() => {
						void refetchRole();
					}}
				/>
			)}
			{showsPeople ? (
				<Tabs
					value={tab === "people" ? "people" : "permissions"}
					onValueChange={(v) => navigate(`/roles/${role.id}/${v}`)}
					className="flex-1 min-h-0 flex flex-col"
				>
					<TabsList className="grid h-auto w-full grid-cols-2 gap-1 group-data-horizontal/tabs:h-auto sm:w-80">
						<TabsTrigger
							value="permissions"
							className="min-h-11 gap-1.5"
						>
							<ShieldCheck className="h-4 w-4" />
							Permissions
						</TabsTrigger>
						<TabsTrigger
							value="people"
							className="min-h-11 gap-1.5"
						>
							<Users className="h-4 w-4" />
							People
						</TabsTrigger>
					</TabsList>
					<TabsContent
						value="permissions"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:overflow-auto">
							<RolePermissionsPanel roleId={role.id} isBuiltin />
						</PageScrollArea>
					</TabsContent>
					<TabsContent
						value="people"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:overflow-auto">
							<RolePeoplePanel roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
				</Tabs>
			) : isBuiltin ? (
				<PageScrollArea className="lg:overflow-auto">
					<RolePermissionsPanel roleId={role.id} isBuiltin />
				</PageScrollArea>
			) : (
				<Tabs
					value={currentTab}
					onValueChange={(v) => navigate(`/roles/${role.id}/${v}`)}
					className="flex-1 min-h-0 flex flex-col"
				>
					<TabsList className="grid h-auto group-data-horizontal/tabs:h-auto w-full grid-cols-2 gap-1 sm:grid-cols-3 xl:grid-cols-6">
						{CONSUMER_TABS.map(({ key, label, Icon }) => {
							const count = role.consumer_counts?.[key];
							return (
								<TabsTrigger
									key={key}
									value={key}
									className="min-h-11 min-w-0 gap-1.5 whitespace-normal"
								>
									<Icon className="h-4 w-4" />
									{label}
									{count !== undefined && (
										<span className="ml-1 text-xs text-muted-foreground">
											{count}
										</span>
									)}
								</TabsTrigger>
							);
						})}
						<TabsTrigger
							value="permissions"
							className="min-h-11 min-w-0 gap-1.5 whitespace-normal"
						>
							<ShieldCheck className="h-4 w-4" />
							Permissions
						</TabsTrigger>
					</TabsList>

					<TabsContent
						value="permissions"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:overflow-auto">
							<RolePermissionsPanel
								roleId={role.id}
								isBuiltin={isBuiltin}
							/>
						</PageScrollArea>
					</TabsContent>

					<TabsContent
						value="users"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
							<UsersTab roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
					<TabsContent
						value="forms"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
							<FormsTab roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
					<TabsContent
						value="agents"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
							<AgentsTab roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
					<TabsContent
						value="apps"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
							<AppsTab roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
					<TabsContent
						value="workflows"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
							<WorkflowsTab roleId={role.id} />
						</PageScrollArea>
					</TabsContent>
				</Tabs>
			)}

			<RoleDialog
				role={role}
				open={editOpen}
				onClose={() => setEditOpen(false)}
			/>

			<RoleDeleteDialog
				name={role.name}
				open={deleteOpen}
				pending={deleteRole.isPending}
				error={
					deleteRole.isError
						? getErrorMessage(
								deleteRole.error,
								"Could not delete the role. Try again.",
							)
						: null
				}
				onOpenChange={setDeleteOpen}
				onDelete={handleDelete}
			/>
		</PageWorkspace>
	);
}

function RoleReadError({
	cached,
	pending,
	onRetry,
}: {
	cached: boolean;
	pending: boolean;
	onRetry: () => void;
}) {
	return (
		<div
			role="alert"
			className="space-y-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
		>
			<p>
				Could not {cached ? "refresh" : "load"} the role.{" "}
				{cached
					? "Previously loaded details are still shown."
					: "Try again to load its details."}
			</p>
			<Button
				variant="outline"
				className="min-h-11"
				disabled={pending}
				onClick={onRetry}
			>
				Retry role
			</Button>
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
		const byId = new Map<
			string,
			components["schemas"]["OrganizationPublic"]
		>();
		for (const o of orgs ?? []) byId.set(o.id, o);
		return (orgId: string | null | undefined) => {
			if (!orgId)
				return { id: null, name: "Platform", isProvider: false };
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
	const navigate = useNavigate();
	const [search, setSearch] = useState("");
	const [offset, setOffset] = useState(0);
	const pageSize = 25;
	const {
		data: assigned,
		isLoading,
		isFetching,
		isError: assignedError,
		refetch: retryAssigned,
	} = useRoleUsersPage(roleId, {
		search,
		limit: pageSize,
		offset,
	});
	const [candidatesRequested, setCandidatesRequested] = useState(false);
	const {
		data: allUsers,
		isLoading: loadingAll,
		isError: candidatesError,
		isFetching: fetchingCandidates,
		refetch: retryCandidates,
	} = useUsersFiltered(undefined, false, candidatesRequested);
	const assignMut = useAssignUsersToRole();
	const unassignMut = useBulkUnassignUsers();
	const orgFor = useOrgLookup();

	const items: ConsumerTabItem[] = useMemo(
		() =>
			(assigned?.users ?? []).map((u) => ({
				id: u.id,
				primary: u.name || u.email,
				secondary: u.name && u.name !== u.email ? u.email : null,
				org: {
					id: u.organization_id ?? null,
					name: u.organization_name ?? "Platform",
					isProvider: u.organization_is_provider,
				},
			})),
		[assigned],
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
			readState={{
				isError: assignedError,
				isFetching,
				onRetry: () => void retryAssigned(),
			}}
			candidates={candidates}
			candidatesLoading={loadingAll}
			candidatesReadState={{
				isError: candidatesError,
				isFetching: fetchingCandidates,
				onRetry: () => void retryCandidates(),
			}}
			consumerLabel="users"
			emptyHint={
				search
					? "No assigned users match your search."
					: "No users assigned to this role yet."
			}
			primaryColumnLabel="Name"
			secondaryColumnLabel="Email"
			showOrgColumn
			searchValue={search}
			onSearchChange={(value) => {
				setSearch(value);
				setOffset(0);
			}}
			pagination={{
				offset,
				limit: pageSize,
				total: assigned?.total ?? 0,
				isFetching,
				onPageChange: setOffset,
			}}
			onItemClick={(item) => navigate(`/users/${item.id}`)}
			getItemHref={(item) => `/users/${item.id}`}
			onRequestCandidates={() => setCandidatesRequested(true)}
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
	const {
		data: assigned,
		isLoading,
		isError: assignedError,
		isFetching,
		refetch: retryAssigned,
	} = useRoleForms(roleId);
	const {
		data: allForms,
		isLoading: loadingAll,
		isError: candidatesError,
		isFetching: fetchingCandidates,
		refetch: retryCandidates,
	} = useForms();
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
			readState={{
				isError: assignedError,
				isFetching,
				onRetry: () => void retryAssigned(),
			}}
			candidates={candidates}
			candidatesLoading={loadingAll}
			candidatesReadState={{
				isError: candidatesError,
				isFetching: fetchingCandidates,
				onRetry: () => void retryCandidates(),
			}}
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
	const {
		data: assigned,
		isLoading,
		isError: assignedError,
		isFetching,
		refetch: retryAssigned,
	} = useRoleAgents(roleId);
	const {
		data: allAgents,
		isLoading: loadingAll,
		isError: candidatesError,
		isFetching: fetchingCandidates,
		refetch: retryCandidates,
	} = useAgents();
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
			readState={{
				isError: assignedError,
				isFetching,
				onRetry: () => void retryAssigned(),
			}}
			candidates={candidates}
			candidatesLoading={loadingAll}
			candidatesReadState={{
				isError: candidatesError,
				isFetching: fetchingCandidates,
				onRetry: () => void retryCandidates(),
			}}
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
	const {
		data: assigned,
		isLoading,
		isError: assignedError,
		isFetching,
		refetch: retryAssigned,
	} = useRoleApps(roleId);
	const {
		data: allAppsResp,
		isLoading: loadingAll,
		isError: candidatesError,
		isFetching: fetchingCandidates,
		refetch: retryCandidates,
	} = useApplications();
	const assignMut = useAssignAppsToRole();
	const unassignMut = useBulkUnassignApps();
	const orgFor = useOrgLookup();

	const allApps = useMemo(() => {
		if (Array.isArray(allAppsResp)) return allAppsResp;
		return (
			(
				allAppsResp as
					| {
							applications?: components["schemas"]["ApplicationPublic"][];
					  }
					| undefined
			)?.applications ?? []
		);
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
			readState={{
				isError: assignedError,
				isFetching,
				onRetry: () => void retryAssigned(),
			}}
			candidates={candidates}
			candidatesLoading={loadingAll}
			candidatesReadState={{
				isError: candidatesError,
				isFetching: fetchingCandidates,
				onRetry: () => void retryCandidates(),
			}}
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
	const {
		data: assigned,
		isLoading,
		isError: assignedError,
		isFetching,
		refetch: retryAssigned,
	} = useRoleWorkflows(roleId);
	const {
		data: allWorkflows,
		isLoading: loadingAll,
		isError: candidatesError,
		isFetching: fetchingCandidates,
		refetch: retryCandidates,
	} = useWorkflows();
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
			readState={{
				isError: assignedError,
				isFetching,
				onRetry: () => void retryAssigned(),
			}}
			candidates={candidates}
			candidatesLoading={loadingAll}
			candidatesReadState={{
				isError: candidatesError,
				isFetching: fetchingCandidates,
				onRetry: () => void retryCandidates(),
			}}
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
